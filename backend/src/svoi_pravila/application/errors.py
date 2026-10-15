"""Application-layer errors."""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum

from svoi_pravila.application.inline_reuse_status import InlineReuseStatus
from svoi_pravila.application.ports.generation import TokenUsage
from svoi_pravila.domain.access import AccessStatus
from svoi_pravila.domain.enums import VariantKind


class UnavailableKind(StrEnum):
    """C0 classifier for generation unavailability (no provider text)."""

    TIMEOUT = "timeout"
    RATE_LIMITED = "rate_limited"
    AUTH = "auth"
    SERVER = "server"
    NETWORK = "network"


class CacheErrorKind(StrEnum):
    """C0 classifier for Valkey/cache failures (no Redis exception text)."""

    NETWORK = "network"
    TIMEOUT = "timeout"
    SERVER = "server"


class InvalidOutputReason(StrEnum):
    """C0 reason a generation result failed validation (one per attempt)."""

    EMPTY_MESSAGE = "empty_message"
    JSON_DECODE = "json_decode"
    SCHEMA_VIOLATION = "schema_violation"
    VARIANT_COUNT = "variant_count"
    VARIANT_KIND_SET = "variant_kind_set"
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
    PROFANITY = "profanity"
    GENERALIZATION = "generalization"


class ApplicationError(Exception):
    """Base class for application errors."""


class CacheUnavailable(ApplicationError):
    """Valkey operation failed; LLM paths fail closed as unavailable."""

    def __init__(self, kind: CacheErrorKind) -> None:
        self.kind = kind
        super().__init__(kind.value)


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


class RuleTextEmpty(ApplicationError):
    """Rule text is empty after normalization."""


class RuleTextTooLong(ApplicationError):
    """Rule text exceeds the maximum Unicode code-point length."""

    def __init__(self, *, maximum: int, actual: int) -> None:
        self.max = maximum
        self.actual = actual
        super().__init__(f"rule text too long: {actual} > {maximum}")


class RuleTextInvalidChars(ApplicationError):
    """Rule text contains forbidden control characters."""


class ContactAlreadyLinked(ApplicationError):
    """Contact is already linked to a pair."""


class ConflictError(ApplicationError):
    """Unique constraint violated (duplicate key)."""


class UsageEventWriteFailed(ApplicationError):
    """Persisting a usage event failed; the user-visible result must not change."""


class AnalyticsErrorKind(StrEnum):
    """Typed ``job_runs.error_kind`` / ``AnalyticsJobFailed.kind`` (never exception text)."""

    UNKNOWN_TIMEZONE = "unknown_timezone"
    CATCH_UP_CAPPED = "catch_up_capped"
    DATABASE = "database"
    NETWORK = "network"
    CANCELLED = "cancelled"


class AnalyticsJobName(StrEnum):
    """Names recorded in ``job_runs.job``."""

    DAILY_AGGREGATES = "daily_aggregates"
    COHORTS = "cohorts"
    PURGE = "purge"


class AnalyticsJobStatus(StrEnum):
    """Lifecycle status of a ``job_runs`` row."""

    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED_LOCKED = "skipped_locked"


class AnalyticsJobFailed(ApplicationError):
    """A daily analytics step failed with a typed kind."""

    def __init__(
        self,
        kind: AnalyticsErrorKind,
        *,
        job: AnalyticsJobName | None = None,
        target_day: date | None = None,
    ) -> None:
        self.kind = kind
        self.job = job
        self.target_day = target_day
        super().__init__(kind.value)


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


class UserQuotaExhausted(ApplicationError):
    """The per-user daily quota class is exhausted (ADR-0009)."""

    def __init__(self, *, resets_at: datetime) -> None:
        self.resets_at = resets_at
        super().__init__("user quota exhausted")


class ServiceBudgetExhausted(ApplicationError):
    """The global daily LLM token budget is exhausted (ADR-0009)."""

    def __init__(self, *, resets_at: datetime) -> None:
        self.resets_at = resets_at
        super().__init__("service budget exhausted")


class IncomingTextTooShort(ApplicationError):
    """Incoming text is shorter than the allowed bound."""


class IncomingTextTooLong(ApplicationError):
    """Incoming text is longer than the allowed bound."""


class InlineQueryTooShort(ApplicationError):
    """Inline query is empty or shorter than the configured minimum."""


class InvalidInlineResultRef(ApplicationError):
    """chosen_inline_result identifier does not encode a known scenario and variant kind."""


class PreparedResultUnavailable(ApplicationError):
    """Prepared-result token is missing, expired, tampered, or bound to another user."""


class RuleSourceUnavailable(ApplicationError):
    """Rule-source token is missing, expired, reused, tampered, or bound to another user."""


class ComposeChoiceTokenInvalid(ApplicationError):
    """Compose choice token is unknown, reused, expired, or not bound to this user."""


class ComposeHandoffUnavailable(ApplicationError):
    """Compose handoff token is missing, expired, reused, or tampered."""


class ComposeSessionUnavailable(ApplicationError):
    """Compose session token is missing, expired, or tampered."""


class RateLimited(ApplicationError):
    """Caller exceeded the configured rate limit."""


class InvalidGenerationOutput(ApplicationError):
    """Model output failed schema or content validation.

    ``slots`` and ``finish_reasons`` are C0 diagnostics, one entry per reason: the rewrite
    slot that was defective and the provider ``finish_reason`` of that attempt. They default
    to ``None`` entries and are set with ``with_diagnostics``.
    """

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
        self.slots: tuple[VariantKind | None, ...] = (None,) * len(reasons)
        self.finish_reasons: tuple[str | None, ...] = (None,) * len(reasons)
        self.usage = usage
        self.attempts = attempts
        self.model = model
        self.prompt_version = prompt_version
        super().__init__("invalid generation output")

    def with_diagnostics(
        self,
        slots: tuple[VariantKind | None, ...],
        finish_reasons: tuple[str | None, ...],
    ) -> InvalidGenerationOutput:
        """Attach per-reason slots and finish_reasons; return ``self`` for raising."""
        if len(slots) != len(self.reasons) or len(finish_reasons) != len(self.reasons):
            msg = "slots and finish_reasons must have one entry per reason"
            raise ValueError(msg)
        self.slots = slots
        self.finish_reasons = finish_reasons
        return self

    def labelled_reasons(self) -> tuple[str, ...]:
        """Reasons as ``reason`` or ``reason:slot`` (C0 diagnostics, no text)."""
        return tuple(
            reason.value if slot is None else f"{reason.value}:{slot.value}"
            for reason, slot in zip(self.reasons, self.slots, strict=True)
        )


InlineProduceError = (
    UserQuotaExhausted
    | ServiceBudgetExhausted
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
