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
from svoi_pravila.adapters.llm.gigachat.validation import invalid, last_reason
from svoi_pravila.application.errors import (
    GenerationRefusedByProvider,
    GenerationUnavailable,
    InvalidGenerationOutput,
    InvalidOutputReason,
    UnavailableKind,
)
from svoi_pravila.application.ports.generation import GenerationMeta


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


class _RetryAttemptError(Exception):
    """Internal signal: first attempt failed with a retryable validation defect."""


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
) -> ModelT:
    usage = token_usage_from_state(state)
    attempts = state.attempts
    raise_for_finish_reason(completion.finish_reason, usage=usage, attempts=attempts)
    if not completion.messages and completion.finish_reason is None:
        raise invalid(InvalidOutputReason.EMPTY_MESSAGE, usage=usage, attempts=attempts)
    raw = _assistant_text(completion)
    if not raw:
        raise invalid(InvalidOutputReason.EMPTY_MESSAGE, usage=usage, attempts=attempts)
    data = json.loads(raw)
    return response_format.model_validate(data)


def _add_length_usage(state: AttemptState, exc: LengthFinishReasonError) -> None:
    completion = exc.completion
    if isinstance(completion, ChatCompletionResponse):
        add_usage(state, completion.usage)


def _record_retryable_invalid(
    state: AttemptState, reason: InvalidOutputReason, *, attempt: int
) -> NoReturn:
    note_invalid_reason(state, reason)
    if should_retry_invalid(attempt=attempt, yielded=None):
        raise _RetryAttemptError
    raise finalize_invalid(state) from None


def _reraise_refused(state: AttemptState) -> NoReturn:
    mark_refused(state)
    raise GenerationRefusedByProvider(
        usage=token_usage_from_state(state),
        attempts=state.attempts,
    ) from None


def _reraise_unavailable(state: AttemptState, kind: UnavailableKind) -> NoReturn:
    mark_unavailable(state, kind)
    raise GenerationUnavailable(
        kind,
        usage=token_usage_from_state(state),
        attempts=state.attempts,
    ) from None


async def _create_once[ModelT: BaseModel](
    params: StructuredCallParams[ModelT],
    state: AttemptState,
) -> ModelT:
    payload: dict[str, object] = {
        "model": params.model,
        "messages": [
            {"role": "system", "content": params.prepared.system},
            {"role": "user", "content": params.prepared.user},
        ],
        "max_tokens": params.max_tokens,
        "response_format": ChatResponseFormat(
            type="json_schema",
            schema=params.response_format,
            strict=True,
        ),
    }
    completion = await params.client.achat.create(payload)
    add_usage(state, completion.usage)
    return _parse_completion(completion, params.response_format, state=state)


def _meta_from_state[ModelT: BaseModel](
    params: StructuredCallParams[ModelT], state: AttemptState
) -> GenerationMeta:
    return GenerationMeta(
        model=params.model,
        prompt_version=params.prepared.prompt_version,
        latency_ms=int((time.perf_counter() - state.started) * 1000),
        attempts=state.attempts,
        usage=token_usage_from_state(state),
    )


async def _run_attempt[ModelT: BaseModel](
    params: StructuredCallParams[ModelT],
    state: AttemptState,
    *,
    attempt: int,
) -> ModelT:
    state.attempts = attempt + 1
    try:
        return await _create_once(params, state)
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
        _record_retryable_invalid(state, InvalidOutputReason.LENGTH, attempt=attempt)
    except ATTEMPT_PROVIDER_EXCEPTIONS as exc:
        mapped = map_provider_exception(
            exc,
            usage=token_usage_from_state(state),
            attempts=state.attempts,
        )
        _reraise_unavailable(state, mapped.kind)


async def _build_result[ModelT: BaseModel, ResultT](
    params: StructuredCallParams[ModelT],
    state: AttemptState,
    build: Callable[[ModelT, GenerationMeta], ResultT],
    *,
    attempt: int,
) -> ResultT:
    parsed = await _run_attempt(params, state, attempt=attempt)
    try:
        return build(parsed, _meta_from_state(params, state))
    except InvalidGenerationOutput as exc:
        _record_retryable_invalid(state, last_reason(exc), attempt=attempt)


async def structured_call[ModelT: BaseModel, ResultT](
    params: StructuredCallParams[ModelT],
    build: Callable[[ModelT, GenerationMeta], ResultT],
    *,
    state: AttemptState,
) -> ResultT:
    """Run achat.create with local validation and one retry on invalid output.

    The caller must own ``asyncio.timeout_at`` for the absolute deadline and
    pass a shared ``AttemptState`` so timeout accounting can include spent tokens.
    """
    try:
        try:
            return await _build_result(params, state, build, attempt=0)
        except _RetryAttemptError:
            return await _build_result(params, state, build, attempt=1)
    finally:
        log_generation_call(
            state,
            operation=params.operation,
            model=params.model,
            prompt_version=params.prepared.prompt_version,
            phase=params.phase,
        )
