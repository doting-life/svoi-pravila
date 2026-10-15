"""Structured GigaChat calls via create + local schema validation."""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import NoReturn

from gigachat import GigaChat, LengthFinishReasonError
from gigachat.models.chat_completions import ChatCompletionResponse, ChatResponseFormat
from pydantic import BaseModel, ValidationError

from svoi_pravila.adapters.llm.gigachat.attempt_policy import (
    ATTEMPT_PROVIDER_EXCEPTIONS,
    AttemptState,
    add_usage,
    finalize_invalid,
    log_generation_call,
    mark_refused,
    mark_unavailable,
    note_invalid_reason,
    should_retry_invalid,
    token_usage_from_state,
)
from svoi_pravila.adapters.llm.gigachat.mapping import (
    map_provider_exception,
    raise_for_finish_reason,
)
from svoi_pravila.adapters.llm.gigachat.prepared import PreparedMessages
from svoi_pravila.adapters.llm.gigachat.prompt_leak import (
    collect_text_fields,
    prompt_leak_reason,
    system_prompt_windows,
)
from svoi_pravila.adapters.llm.gigachat.repair import (
    FirstAttemptDefect,
    first_attempt_defect,
    retry_messages,
)
from svoi_pravila.adapters.llm.gigachat.validation import (
    SlotDefect,
    SlotDefectsError,
    invalid,
    last_reason,
)
from svoi_pravila.application.errors import (
    GenerationRefusedByProvider,
    GenerationUnavailable,
    InvalidGenerationOutput,
    InvalidOutputReason,
    UnavailableKind,
)
from svoi_pravila.application.ports.generation import GenerationMeta
from svoi_pravila.application.ports.generation_attempt_observer import (
    GenerationAttemptObserver,
    InvalidAttempt,
)
from svoi_pravila.domain.enums import VariantKind
from svoi_pravila.domain.lexicon_guard import (
    FindingKind,
    GuardLexicons,
    SlotFinding,
    find_in_slots,
)


@dataclass(frozen=True, slots=True)
class SlotGuard[ModelT: BaseModel]:
    """The lexicon check of one operation: the lexicons and the slots of a parsed answer.

    ``slot_texts`` returns the ``softer``/``firmer`` texts of a parsed answer, or nothing when
    the answer carries no variants (a non-ok safety verdict). It never returns ``check``.
    """

    lexicons: GuardLexicons
    slot_texts: Callable[[ModelT], Mapping[VariantKind, str]]


@dataclass(frozen=True, slots=True)
class StructuredCallParams[ModelT: BaseModel]:
    """Arguments for one structured generation call (no nested timeout).

    ``guard`` enables the lexicon check (obscene words, generalizations) of the answer;
    ``observer`` receives every invalid attempt.
    """

    client: GigaChat
    operation: str
    model: str
    prepared: PreparedMessages
    response_format: type[ModelT]
    max_tokens: int
    phase: str | None = None
    top_p: float | None = None
    guard: SlotGuard[ModelT] | None = None
    observer: GenerationAttemptObserver | None = None


class _RetryAttemptError(Exception):
    """Internal signal: the first attempt failed with a defect the second attempt corrects.

    ``defect`` (reason, slot and, when echoed, the first answer's JSON) travels with it.
    """

    def __init__(self, defect: FirstAttemptDefect) -> None:
        self.defect = defect
        super().__init__(",".join(defect.labels))


@dataclass(frozen=True, slots=True)
class LabelledInvalidAttempt(InvalidAttempt):
    """An invalid attempt that also names every defect of the answer.

    ``labels`` are C0 ``reason`` or ``reason:slot`` labels, one per defect or finding of that
    attempt, never text. An observer that does not know this type reads ``reason`` and ``slot``.
    """

    labels: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class _Answer[ModelT: BaseModel]:
    """A parsed answer and the JSON text it was parsed from."""

    parsed: ModelT
    raw: str


def _assistant_text(completion: ChatCompletionResponse) -> str:
    for message in completion.messages or []:
        if message.role != "assistant" or not message.content:
            continue
        text = "".join(part.text for part in message.content if part.text)
        if text:
            return text
    return ""


def _parse_completion[ModelT: BaseModel](
    completion: ChatCompletionResponse,
    response_format: type[ModelT],
    *,
    state: AttemptState,
) -> _Answer[ModelT]:
    usage = token_usage_from_state(state)
    attempts = state.attempts
    raise_for_finish_reason(
        completion.finish_reason,
        usage=usage,
        attempts=attempts,
        model=state.model,
        prompt_version=state.prompt_version,
    )
    if not completion.messages and completion.finish_reason is None:
        raise invalid(
            InvalidOutputReason.EMPTY_MESSAGE,
            usage=usage,
            attempts=attempts,
            model=state.model,
            prompt_version=state.prompt_version,
        )
    raw = _assistant_text(completion)
    if not raw:
        raise invalid(
            InvalidOutputReason.EMPTY_MESSAGE,
            usage=usage,
            attempts=attempts,
            model=state.model,
            prompt_version=state.prompt_version,
        )
    data = json.loads(raw)
    return _Answer(parsed=response_format.model_validate(data), raw=raw)


def _add_length_usage(state: AttemptState, exc: LengthFinishReasonError) -> None:
    completion = exc.completion
    if isinstance(completion, ChatCompletionResponse):
        add_usage(state, completion.usage)


def _field_texts(value: object, prefix: str = "") -> tuple[tuple[str, str], ...]:
    """Named string leaves of a dumped answer: ``softer``, ``variants[0].text`` ..."""
    if isinstance(value, str):
        return ((prefix, value),)
    if isinstance(value, Mapping):
        return tuple(
            pair
            for key, item in value.items()
            for pair in _field_texts(item, f"{prefix}.{key}" if prefix else str(key))
        )
    if isinstance(value, list):
        return tuple(
            pair
            for index, item in enumerate(value)
            for pair in _field_texts(item, f"{prefix}[{index}]")
        )
    return ()


@dataclass(frozen=True, slots=True)
class _InvalidAnswer[ModelT: BaseModel]:
    """What is known about an invalid attempt: the reason, slot, answer, defects and findings.

    ``reason`` and ``slot`` are the first defect; ``defects`` are all the slot defects of the
    answer when slot validation found them.
    """

    reason: InvalidOutputReason
    slot: VariantKind | None = None
    answer: _Answer[ModelT] | None = None
    findings: tuple[SlotFinding, ...] = ()
    defects: tuple[SlotDefect, ...] = ()


def _raw_texts(state: AttemptState) -> tuple[tuple[str, str], ...]:
    return (("raw", state.raw_answer),) if state.raw_answer else ()


def _record_retryable_invalid[ModelT: BaseModel](
    params: StructuredCallParams[ModelT],
    state: AttemptState,
    invalid_answer: _InvalidAnswer[ModelT],
    *,
    attempt: int,
) -> NoReturn:
    reason, slot, answer = invalid_answer.reason, invalid_answer.slot, invalid_answer.answer
    note_invalid_reason(state, reason, slot)
    raw = None if answer is None else answer.raw
    defect = first_attempt_defect(
        reason, slot, raw, invalid_answer.findings, invalid_answer.defects
    )
    if params.observer is not None:
        texts = _raw_texts(state) if answer is None else _field_texts(answer.parsed.model_dump())
        params.observer.on_invalid_attempt(
            LabelledInvalidAttempt(
                operation=params.operation,
                attempt=attempt,
                reason=reason,
                slot=slot,
                texts=texts,
                labels=defect.labels,
            )
        )
    if should_retry_invalid(attempt=attempt, yielded=None):
        raise _RetryAttemptError(defect)
    raise finalize_invalid(state) from None


def _reraise_refused(state: AttemptState) -> NoReturn:
    mark_refused(state)
    raise GenerationRefusedByProvider(
        usage=token_usage_from_state(state),
        attempts=state.attempts,
        model=state.model,
        prompt_version=state.prompt_version,
    ) from None


def _reraise_unavailable(state: AttemptState, kind: UnavailableKind) -> NoReturn:
    mark_unavailable(state, kind)
    raise GenerationUnavailable(
        kind,
        usage=token_usage_from_state(state),
        attempts=state.attempts,
        model=state.model,
        prompt_version=state.prompt_version,
    ) from None


def _request_messages(
    prepared: PreparedMessages, defect: FirstAttemptDefect | None
) -> list[dict[str, str]]:
    if defect is not None:
        return retry_messages(prepared, defect)
    return [
        {"role": "system", "content": prepared.system},
        {"role": "user", "content": prepared.user},
    ]


async def _create_once[ModelT: BaseModel](
    params: StructuredCallParams[ModelT],
    state: AttemptState,
    defect: FirstAttemptDefect | None,
) -> _Answer[ModelT]:
    payload: dict[str, object] = {
        "model": params.model,
        "messages": _request_messages(params.prepared, defect),
        "max_tokens": params.max_tokens,
        "response_format": ChatResponseFormat(
            type="json_schema",
            schema=params.response_format,
            strict=True,
        ),
    }
    if params.top_p is not None:
        payload["top_p"] = params.top_p
    completion = await params.client.achat.create(payload)
    state.finish_reason = completion.finish_reason
    state.raw_answer = _assistant_text(completion)
    add_usage(state, completion.usage)
    return _parse_completion(completion, params.response_format, state=state)


def _meta_from_state[ModelT: BaseModel](
    params: StructuredCallParams[ModelT],
    state: AttemptState,
    repaired: tuple[str, ...],
    residual: tuple[VariantKind, ...] = (),
) -> GenerationMeta:
    return GenerationMeta(
        model=params.model,
        prompt_version=params.prepared.prompt_version,
        latency_ms=int((time.perf_counter() - state.started) * 1000),
        attempts=state.attempts,
        usage=token_usage_from_state(state),
        repaired=repaired,
        residual=residual,
    )


async def _run_attempt[ModelT: BaseModel](
    params: StructuredCallParams[ModelT],
    state: AttemptState,
    *,
    attempt: int,
    defect: FirstAttemptDefect | None,
) -> _Answer[ModelT]:
    state.attempts = attempt + 1
    state.finish_reason = None
    state.raw_answer = ""
    try:
        return await _create_once(params, state, defect)
    except GenerationRefusedByProvider:
        _reraise_refused(state)
    except InvalidGenerationOutput as exc:
        _record_retryable_invalid(params, state, _InvalidAnswer(last_reason(exc)), attempt=attempt)
    except ValidationError:
        _record_retryable_invalid(
            params, state, _InvalidAnswer(InvalidOutputReason.SCHEMA_VIOLATION), attempt=attempt
        )
    except json.JSONDecodeError:
        _record_retryable_invalid(
            params, state, _InvalidAnswer(InvalidOutputReason.JSON_DECODE), attempt=attempt
        )
    except LengthFinishReasonError as exc:
        _add_length_usage(state, exc)
        state.finish_reason = "length"
        _record_retryable_invalid(
            params, state, _InvalidAnswer(InvalidOutputReason.LENGTH), attempt=attempt
        )
    except ATTEMPT_PROVIDER_EXCEPTIONS as exc:
        mapped = map_provider_exception(
            exc,
            usage=token_usage_from_state(state),
            attempts=state.attempts,
            model=state.model,
            prompt_version=state.prompt_version,
        )
        _reraise_unavailable(state, mapped.kind)


async def _build_result[ModelT: BaseModel, ResultT](
    params: StructuredCallParams[ModelT],
    state: AttemptState,
    build: Callable[[ModelT, GenerationMeta], ResultT],
    *,
    attempt: int,
    defect: FirstAttemptDefect | None,
) -> ResultT:
    answer = await _run_attempt(params, state, attempt=attempt, defect=defect)
    leak = prompt_leak_reason(
        collect_text_fields(answer.parsed.model_dump()),
        windows=system_prompt_windows(params.prepared.system),
        marker=params.prepared.boundary_marker,
        untrusted=params.prepared.untrusted,
    )
    if leak is not None:
        _record_retryable_invalid(
            params, state, _InvalidAnswer(leak, answer=answer), attempt=attempt
        )
    findings = _guard_findings(params, answer.parsed)
    residual = tuple(item.slot for item in findings if item.kind is FindingKind.GENERALIZATION)
    repaired = () if defect is None else defect.labels
    try:
        result = build(answer.parsed, _meta_from_state(params, state, repaired, residual))
    except InvalidGenerationOutput as exc:
        _record_retryable_invalid(
            params,
            state,
            _InvalidAnswer(
                last_reason(exc),
                slot=exc.slots[-1],
                answer=answer,
                defects=exc.defects if isinstance(exc, SlotDefectsError) else (),
            ),
            attempt=attempt,
        )
    if findings:
        _decide_findings(params, state, findings, attempt=attempt, answer=answer)
    return result


def _guard_findings[ModelT: BaseModel](
    params: StructuredCallParams[ModelT], parsed: ModelT
) -> tuple[SlotFinding, ...]:
    if params.guard is None:
        return ()
    return find_in_slots(params.guard.slot_texts(parsed), params.guard.lexicons)


def _decide_findings[ModelT: BaseModel](
    params: StructuredCallParams[ModelT],
    state: AttemptState,
    findings: tuple[SlotFinding, ...],
    *,
    attempt: int,
    answer: _Answer[ModelT],
) -> None:
    """Retry once on any finding; after the retry only obscene words fail the call.

    A surviving generalization is accepted: the caller reads it from ``GenerationMeta.residual``.
    """
    obscene = next((item for item in findings if item.kind is FindingKind.PROFANITY), None)
    primary = findings[0] if obscene is None else obscene
    if should_retry_invalid(attempt=attempt, yielded=None) or obscene is not None:
        _record_retryable_invalid(
            params,
            state,
            _InvalidAnswer(
                InvalidOutputReason(primary.kind.value),
                slot=primary.slot,
                answer=answer,
                findings=findings,
            ),
            attempt=attempt,
        )


async def structured_call[ModelT: BaseModel, ResultT](
    params: StructuredCallParams[ModelT],
    build: Callable[[ModelT, GenerationMeta], ResultT],
    *,
    state: AttemptState,
) -> ResultT:
    """Run achat.create with local validation and one corrective retry on invalid output.

    The second request repairs the first attempt's defect: it carries the correction text
    and, when the first answer parsed as JSON and the defect allows it, that answer.

    The caller must own ``asyncio.timeout_at`` for the absolute deadline and
    pass a shared ``AttemptState`` so timeout accounting can include spent tokens.
    """
    try:
        try:
            return await _build_result(params, state, build, attempt=0, defect=None)
        except _RetryAttemptError as retry:
            return await _build_result(params, state, build, attempt=1, defect=retry.defect)
    finally:
        log_generation_call(
            state,
            operation=params.operation,
            model=params.model,
            prompt_version=params.prepared.prompt_version,
            phase=params.phase,
        )
