"""Shared attempt policy for structured and streaming GigaChat calls."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import httpx
import structlog
from gigachat import (
    AuthenticationError,
    ForbiddenError,
    RateLimitError,
    ResponseError,
    ServerError,
)
from gigachat.models.chat_completions import ChatUsage

from svoi_pravila.application.errors import (
    InvalidGenerationOutput,
    InvalidOutputReason,
    UnavailableKind,
)
from svoi_pravila.application.ports.generation import TokenUsage

logger = structlog.get_logger(__name__)

# Provider errors handled inside attempt loops (TimeoutError owned by deadline boundary).
ATTEMPT_PROVIDER_EXCEPTIONS = (
    AuthenticationError,
    ForbiddenError,
    RateLimitError,
    ServerError,
    ResponseError,
    httpx.HTTPError,
)


@dataclass(slots=True)
class AttemptState:
    """Mutable counters and outcome for one generation_call log record."""

    started: float
    model: str
    prompt_version: str
    outcome: str = "ok"
    attempts: int = field(default=0)
    input_tokens: int = field(default=0)
    output_tokens: int = field(default=0)
    precached_prompt_tokens: int = field(default=0)
    log_reasons: tuple[str, ...] = ()
    unavailable_kind: str | None = None
    collected_reasons: list[InvalidOutputReason] = field(default_factory=list)


def token_usage_from_state(state: AttemptState) -> TokenUsage:
    """Build a TokenUsage snapshot from attempt counters."""
    return TokenUsage(
        input=state.input_tokens,
        output=state.output_tokens,
        precached=state.precached_prompt_tokens,
    )


def add_usage(state: AttemptState, usage: ChatUsage | None) -> None:
    """Accumulate provider usage across attempts (including failed ones)."""
    if usage is None:
        return
    input_tokens = int(usage.input_tokens or 0)
    output_tokens = int(usage.output_tokens or 0)
    precached = 0
    if usage.input_tokens_details is not None:
        precached = int(usage.input_tokens_details.cached_tokens or 0)
    state.input_tokens += input_tokens
    state.output_tokens += output_tokens
    state.precached_prompt_tokens += precached


def should_retry_invalid(*, attempt: int, yielded: bool | None) -> bool:
    """Return whether a validation failure may be retried."""
    if yielded is True:
        return False
    return attempt == 0


def note_invalid_reason(state: AttemptState, reason: InvalidOutputReason) -> None:
    """Record one attempt's InvalidOutputReason."""
    state.collected_reasons.append(reason)


def finalize_invalid(state: AttemptState) -> InvalidGenerationOutput:
    """Mark invalid_output and build the multi-reason application error."""
    state.outcome = "invalid_output"
    state.log_reasons = tuple(r.value for r in state.collected_reasons)
    return InvalidGenerationOutput(
        tuple(state.collected_reasons),
        usage=token_usage_from_state(state),
        attempts=len(state.collected_reasons),
        model=state.model,
        prompt_version=state.prompt_version,
    )


def mark_refused(state: AttemptState) -> None:
    """Mark provider refusal outcome."""
    state.outcome = "refused"


def mark_unavailable(state: AttemptState, kind: UnavailableKind) -> None:
    """Mark unavailable outcome with C0 kind."""
    state.outcome = "unavailable"
    state.unavailable_kind = kind.value


def log_generation_call(
    state: AttemptState,
    *,
    operation: str,
    model: str,
    prompt_version: str,
    phase: str | None = None,
) -> None:
    """Emit one C0 generation_call log line."""
    log_kwargs: dict[str, object] = {
        "operation": operation,
        "model": model,
        "prompt_version": prompt_version,
        "latency_ms": int((time.perf_counter() - state.started) * 1000),
        "input_tokens": state.input_tokens,
        "output_tokens": state.output_tokens,
        "precached_prompt_tokens": state.precached_prompt_tokens,
        "outcome": state.outcome,
        "attempts": state.attempts,
        "reasons": state.log_reasons,
        "unavailable_kind": state.unavailable_kind,
    }
    if phase is not None:
        log_kwargs["phase"] = phase
    logger.info("generation_call", **log_kwargs)
