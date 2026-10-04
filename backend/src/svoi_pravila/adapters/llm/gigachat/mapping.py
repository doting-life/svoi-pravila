"""Map provider exceptions and finish reasons to application errors."""

from __future__ import annotations

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

from svoi_pravila.adapters.llm.gigachat.attempt_policy import ATTEMPT_PROVIDER_EXCEPTIONS
from svoi_pravila.application.errors import (
    GenerationRefusedByProvider,
    GenerationUnavailable,
    InvalidGenerationOutput,
    InvalidOutputReason,
    UnavailableKind,
)
from svoi_pravila.application.ports.generation import TokenUsage

logger = structlog.get_logger(__name__)

_HTTP_UNAUTHORIZED = 401
_HTTP_FORBIDDEN = 403
_HTTP_TOO_MANY_REQUESTS = 429
_HTTP_SERVER_ERROR_MIN = 500

# Exceptions list-models / outer catches may observe (Length/Timeout owned elsewhere).
PROVIDER_EXCEPTIONS = ATTEMPT_PROVIDER_EXCEPTIONS


def raise_for_finish_reason(
    finish_reason: str | None,
    *,
    usage: TokenUsage,
    attempts: int,
    model: str,
    prompt_version: str,
) -> None:
    """Translate provider finish_reason into an application error when needed."""
    if finish_reason == "blacklist":
        raise GenerationRefusedByProvider(
            usage=usage,
            attempts=attempts,
            model=model,
            prompt_version=prompt_version,
        )
    if finish_reason == "length":
        raise InvalidGenerationOutput(
            (InvalidOutputReason.LENGTH,),
            usage=usage,
            attempts=attempts,
            model=model,
            prompt_version=prompt_version,
        )


def _unavailable(
    kind: UnavailableKind,
    *,
    usage: TokenUsage,
    attempts: int,
    model: str,
    prompt_version: str,
) -> GenerationUnavailable:
    return GenerationUnavailable(
        kind,
        usage=usage,
        attempts=attempts,
        model=model,
        prompt_version=prompt_version,
    )


def _kind_for_response_error(exc: ResponseError) -> UnavailableKind | None:
    status = exc.status_code
    if status in {_HTTP_UNAUTHORIZED, _HTTP_FORBIDDEN}:
        logger.info("generation_auth_failed")
        return UnavailableKind.AUTH
    if status == _HTTP_TOO_MANY_REQUESTS:
        return UnavailableKind.RATE_LIMITED
    if status >= _HTTP_SERVER_ERROR_MIN:
        return UnavailableKind.SERVER
    return None


def map_provider_exception(
    exc: BaseException,
    *,
    usage: TokenUsage,
    attempts: int,
    model: str,
    prompt_version: str,
) -> GenerationUnavailable:
    """Return GenerationUnavailable for a known HTTP/provider exception.

    Unknown exceptions (including unexpected 4xx ResponseError) are re-raised unchanged.
    LengthFinishReasonError and TimeoutError are handled at their owning sites.
    """
    kind: UnavailableKind | None = None
    if isinstance(exc, (AuthenticationError, ForbiddenError)):
        logger.info("generation_auth_failed")
        kind = UnavailableKind.AUTH
    elif isinstance(exc, RateLimitError):
        kind = UnavailableKind.RATE_LIMITED
    elif isinstance(exc, ServerError):
        kind = UnavailableKind.SERVER
    elif isinstance(exc, ResponseError):
        kind = _kind_for_response_error(exc)
        if kind is None:
            raise exc
    elif isinstance(exc, httpx.HTTPError):
        kind = UnavailableKind.NETWORK
    else:
        raise exc
    return _unavailable(
        kind,
        usage=usage,
        attempts=attempts,
        model=model,
        prompt_version=prompt_version,
    )


def usage_tokens(usage: ChatUsage | None) -> tuple[int, int]:
    """Extract input/output token counts from a typed ChatUsage object."""
    if usage is None:
        return 0, 0
    return int(usage.input_tokens or 0), int(usage.output_tokens or 0)
