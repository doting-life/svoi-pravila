"""Phase-A plain-text analysis streaming for two-phase decode."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from typing import NoReturn

from gigachat import GigaChat, LengthFinishReasonError
from gigachat.models import ChatContentPart
from gigachat.models.chat_completions import ChatCompletionChunk, ChatCompletionResponse, ChatUsage

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
from svoi_pravila.adapters.llm.gigachat.prompt_leak import prompt_leak_reason, system_prompt_windows
from svoi_pravila.adapters.llm.gigachat.validation import (
    MAX_TOKENS_ANALYSIS,
    analysis_reason,
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
from svoi_pravila.application.ports.generation import AnalysisChunk, TokenUsage


@dataclass(frozen=True, slots=True)
class AnalysisPhaseResult:
    """Validated full analysis text plus phase-A token/attempt counters."""

    text: str
    usage: TokenUsage
    attempts: int


@dataclass(frozen=True, slots=True)
class AnalysisStreamParams:
    """Arguments for phase-A analysis streaming."""

    client: GigaChat
    model: str
    prepared: PreparedMessages
    started: float
    prompt_version: str


class _RetryAttemptError(Exception):
    """Internal signal: first analysis attempt failed before any yield."""


def _content_text(parts: Sequence[ChatContentPart] | None) -> str:
    if not parts:
        return ""
    return "".join(part.text for part in parts if part.text)


def _chunk_text(chunk: ChatCompletionChunk) -> str:
    return "".join(_content_text(message.content) for message in chunk.messages or [])


def _add_length_usage(state: AttemptState, exc: LengthFinishReasonError) -> None:
    completion = exc.completion
    if isinstance(completion, ChatCompletionResponse):
        add_usage(state, completion.usage)


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


def _handle_provider_exc(state: AttemptState, exc: BaseException) -> NoReturn:
    mapped = map_provider_exception(
        exc,
        usage=token_usage_from_state(state),
        attempts=state.attempts,
        model=state.model,
        prompt_version=state.prompt_version,
    )
    _reraise_unavailable(state, mapped.kind)


class AnalysisPhase:
    """Phase-A runner: stream chunks, then expose a typed ``result``."""

    def __init__(self, params: AnalysisStreamParams) -> None:
        self._params = params
        self._state = AttemptState(
            started=params.started,
            model=params.model,
            prompt_version=params.prompt_version,
        )
        self._completed: list[AnalysisPhaseResult] = []

    @property
    def state(self) -> AttemptState:
        """Mutable attempt counters (for deadline timeout accounting)."""
        return self._state

    @property
    def result(self) -> AnalysisPhaseResult:
        """Validated analysis after ``stream()`` completes successfully."""
        return self._completed[0]

    @property
    def completed(self) -> bool:
        """Whether phase A produced a validated result."""
        return bool(self._completed)

    async def stream(self) -> AsyncIterator[AnalysisChunk]:
        """Yield analysis chunks; set ``result`` on success; raise on failure.

        The caller must own ``asyncio.timeout_at`` for the absolute deadline.
        """
        try:
            try:
                async for chunk in self._stream_attempt(attempt=0):
                    yield chunk
            except _RetryAttemptError:
                async for chunk in self._stream_attempt(attempt=1):
                    yield chunk
        finally:
            log_generation_call(
                self._state,
                operation="decode_stream",
                model=self._params.model,
                prompt_version=self._params.prepared.prompt_version,
                phase="analysis",
            )

    async def _stream_attempt(self, *, attempt: int) -> AsyncIterator[AnalysisChunk]:
        self._state.attempts = attempt + 1
        yielded = False
        buffer = ""
        last_usage: ChatUsage | None = None
        stream_request: dict[str, object] = {
            "model": self._params.model,
            "messages": [
                {"role": "system", "content": self._params.prepared.system},
                {"role": "user", "content": self._params.prepared.user},
            ],
            "max_tokens": MAX_TOKENS_ANALYSIS,
        }
        try:
            async for chunk in self._params.client.achat.stream(stream_request):
                typed_chunk: ChatCompletionChunk = chunk
                if typed_chunk.finish_reason:
                    raise_for_finish_reason(
                        typed_chunk.finish_reason,
                        usage=token_usage_from_state(self._state),
                        attempts=self._state.attempts,
                        model=self._state.model,
                        prompt_version=self._state.prompt_version,
                    )
                if typed_chunk.usage is not None:
                    last_usage = typed_chunk.usage
                text = _chunk_text(typed_chunk)
                if not text:
                    continue
                buffer += text
                yielded = True
                yield AnalysisChunk(text=text)
            add_usage(self._state, last_usage)
            leak = prompt_leak_reason(
                (buffer,),
                windows=system_prompt_windows(self._params.prepared.system),
            )
            defect = leak if leak is not None else analysis_reason(buffer)
            if defect is not None:
                raise invalid(
                    defect,
                    usage=token_usage_from_state(self._state),
                    attempts=self._state.attempts,
                    model=self._state.model,
                    prompt_version=self._state.prompt_version,
                )
            self._completed.append(
                AnalysisPhaseResult(
                    text=buffer,
                    usage=token_usage_from_state(self._state),
                    attempts=self._state.attempts,
                )
            )
        except GenerationRefusedByProvider:
            _reraise_refused(self._state)
        except InvalidGenerationOutput as exc:
            note_invalid_reason(self._state, last_reason(exc))
            if should_retry_invalid(attempt=attempt, yielded=yielded):
                raise _RetryAttemptError from None
            raise finalize_invalid(self._state) from None
        except LengthFinishReasonError as exc:
            _add_length_usage(self._state, exc)
            note_invalid_reason(self._state, InvalidOutputReason.LENGTH)
            if should_retry_invalid(attempt=attempt, yielded=yielded):
                raise _RetryAttemptError from None
            raise finalize_invalid(self._state) from None
        except ATTEMPT_PROVIDER_EXCEPTIONS as exc:
            _handle_provider_exc(self._state, exc)
