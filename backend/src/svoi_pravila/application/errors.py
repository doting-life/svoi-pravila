"""Application-layer errors."""

from __future__ import annotations

from enum import StrEnum

from svoi_pravila.application.inline_reuse_status import InlineReuseStatus
from svoi_pravila.application.ports.generation import TokenUsage
from svoi_pravila.domain.access import AccessStatus


class UnavailableKind(StrEnum):
    """C0 classifier for generation unavailability (no provider text)."""

    TIMEOUT = "timeout"
    RATE_LIMITED = "rate_limited"
    AUTH = "auth"
    SERVER = "server"
    NETWORK = "network"


class InvalidOutputReason(StrEnum):
    """C0 reason a generation result failed validation (one per attempt)."""

    EMPTY_MESSAGE = "empty_message"
    JSON_DECODE = "json_decode"
    SCHEMA_VIOLATION = "schema_violation"
    VARIANT_COUNT = "variant_count"
    FIRMNESS_SET = "firmness_set"
    RULE_INDEX_OUT_OF_RANGE = "rule_index_out_of_range"
    URL_IN_TEXT = "url_in_text"
    MARKUP_FENCE = "markup_fence"
    EMPTY_TEXT = "empty_text"
    TEXT_TOO_LONG = "text_too_long"
    LENGTH = "length"
    ANALYSIS_TOO_LONG = "analysis_too_long"
    HYPOTHESIS_COUNT = "hypothesis_count"
    BOUNDARY_COLLISION = "boundary_collision"
    PROMPT_LEAK = "prompt_leak"


class ApplicationError(Exception):
    """Base class for application errors."""


class NotFound(ApplicationError):
    """Entity not found or not visible to the actor (no existence leak)."""


class AccessNotGranted(ApplicationError):
    """Protected operation requires completed access steps."""

    def __init__(self, status: AccessStatus) -> None:
        self.status = status
        super().__init__("access not granted")


class AlreadyPaired(ApplicationError):
    """A pair between the two users already exists."""


class ContactLimitReached(ApplicationError):
    """Owner already has the maximum number of contacts."""


class OpenRuleLimitReached(ApplicationError):
    """Scope already has the maximum number of open rules."""


class ContactAlreadyLinked(ApplicationError):
    """Contact is already linked to a pair."""


class ConflictError(ApplicationError):
    """Unique constraint violated (duplicate key)."""


class UsageEventWriteFailed(ApplicationError):
    """Persisting a usage event failed; the user-visible result must not change."""


class GenerationUnavailable(ApplicationError):
    """Generation provider is unavailable, timed out, or rate-limited."""

    def __init__(
        self,
        kind: UnavailableKind,
        *,
        usage: TokenUsage,
        attempts: int,
        model: str,
        prompt_version: str,
    ) -> None:
        self.kind = kind
        self.usage = usage
        self.attempts = attempts
        self.model = model
        self.prompt_version = prompt_version
        super().__init__("generation unavailable")


class GenerationRefusedByProvider(ApplicationError):
    """Provider moderation refused to generate a completion."""

    def __init__(
        self,
        *,
        usage: TokenUsage,
        attempts: int,
        model: str,
        prompt_version: str,
    ) -> None:
        self.usage = usage
        self.attempts = attempts
        self.model = model
        self.prompt_version = prompt_version
        super().__init__("generation refused by provider")


class ScenarioBusy(ApplicationError):
    """A decode (or other scenario) is already in flight for this user."""


class ScenarioQuotaExceeded(ApplicationError):
    """The per-user scenario quota window is exhausted."""


class IncomingTextTooShort(ApplicationError):
    """Incoming text is shorter than the allowed bound."""


class IncomingTextTooLong(ApplicationError):
    """Incoming text is longer than the allowed bound."""


class InlineQueryTooShort(ApplicationError):
    """Inline query is empty or shorter than the configured minimum."""


class InvalidInlineResultRef(ApplicationError):
    """chosen_inline_result identifier does not encode a known scenario and firmness."""


class PreparedResultUnavailable(ApplicationError):
    """Prepared-result token is missing, expired, tampered, or bound to another user."""


class BotChatUnavailable(ApplicationError):
    """The user has blocked the bot or never started a private chat."""


class RuleSourceUnavailable(ApplicationError):
    """Rule-source token is missing, expired, reused, tampered, or bound to another user."""


class InvalidGenerationOutput(ApplicationError):
    """Model output failed schema or content validation."""

    def __init__(
        self,
        reasons: tuple[InvalidOutputReason, ...],
        *,
        usage: TokenUsage,
        attempts: int,
        model: str,
        prompt_version: str,
    ) -> None:
        if not reasons:
            msg = "InvalidGenerationOutput requires at least one reason"
            raise ValueError(msg)
        self.reasons = reasons
        self.usage = usage
        self.attempts = attempts
        self.model = model
        self.prompt_version = prompt_version
        super().__init__("invalid generation output")


InlineProduceError = (
    ScenarioQuotaExceeded
    | GenerationUnavailable
    | GenerationRefusedByProvider
    | InvalidGenerationOutput
)


class InlineComposeFailed(ApplicationError):
    """Produce failed for one inline waiter; wraps the typed cause."""

    def __init__(self, cause: InlineProduceError, *, reuse: InlineReuseStatus) -> None:
        self.cause = cause
        self.reuse = reuse
        super().__init__("inline compose failed")
