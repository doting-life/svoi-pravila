"""Structured GigaChat calls via create + local schema validation."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
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
from svoi_pravila.adapters.llm.gigachat.validation import invalid, last_reason
from svoi_pravila.application.errors import (
    GenerationRefusedByProvider,
    GenerationUnavailable,
    InvalidGenerationOutput,
    InvalidOutputReason,
    UnavailableKind,
)
from svoi_pravila.application.ports.generation import GenerationMeta
from svoi_pravila.domain.enums import VariantKind


@dataclass(frozen=True, slots=True)
class StructuredCallParams[ModelT: BaseModel]:
    """Arguments for one structured generation call (no nested timeout)."""

    client: GigaChat
    operation: str
    model: str
    prepared: PreparedMessages
    response_format: type[ModelT]
    max_tokens: int
    phase: str | None = None
    top_p: float | None = None


class _RetryAttemptError(Exception):
    """Internal signal: the first attempt failed with a defect the second attempt corrects.

    ``defect`` (reason, slot and, when echoed, the first answer's JSON) travels with it.
    """

    def __init__(self, defect: FirstAttemptDefect) -> None:
        self.defect = defect
        super().__init__(defect.label)


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


def _record_retryable_invalid(
    state: AttemptState,
    reason: InvalidOutputReason,
    *,
    attempt: int,
    slot: VariantKind | None = None,
    answer: str | None = None,
) -> NoReturn:
    note_invalid_reason(state, reason, slot)
    if should_retry_invalid(attempt=attempt, yielded=None):
        raise _RetryAttemptError(first_attempt_defect(reason, slot, answer))
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
    add_usage(state, completion.usage)
    return _parse_completion(completion, params.response_format, state=state)


def _meta_from_state[ModelT: BaseModel](
    params: StructuredCallParams[ModelT],
    state: AttemptState,
    repaired: tuple[str, ...],
) -> GenerationMeta:
    return GenerationMeta(
        model=params.model,
        prompt_version=params.prepared.prompt_version,
        latency_ms=int((time.perf_counter() - state.started) * 1000),
        attempts=state.attempts,
        usage=token_usage_from_state(state),
        repaired=repaired,
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
    try:
        return await _create_once(params, state, defect)
    except GenerationRefusedByProvider:
        _reraise_refused(state)
    except InvalidGenerationOutput as exc:
        _record_retryable_invalid(state, last_reason(exc), attempt=attempt)
    except ValidationError:
        _record_retryable_invalid(state, InvalidOutputReason.SCHEMA_VIOLATION, attempt=attempt)
    except json.JSONDecodeError:
        _record_retryable_invalid(state, InvalidOutputReason.JSON_DECODE, attempt=attempt)
    except LengthFinishReasonError as exc:
        _add_length_usage(state, exc)
        state.finish_reason = "length"
        _record_retryable_invalid(state, InvalidOutputReason.LENGTH, attempt=attempt)
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
        _record_retryable_invalid(state, leak, attempt=attempt, answer=answer.raw)
    repaired = () if defect is None else (defect.label,)
    try:
        return build(answer.parsed, _meta_from_state(params, state, repaired))
    except InvalidGenerationOutput as exc:
        _record_retryable_invalid(
            state, last_reason(exc), attempt=attempt, slot=exc.slots[-1], answer=answer.raw
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
