"""C0 usage-event record (no conversation text, no Telegram identifiers)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from svoi_pravila.domain.enums import (
    Firmness,
    UsageEventKind,
    UsageOutcome,
    UsageScenario,
    UsageSurface,
)
from svoi_pravila.domain.errors import InvalidValueError
from svoi_pravila.domain.ids import UsageEventId
from svoi_pravila.domain.time import require_utc

_HEX64 = 64


@dataclass(frozen=True, slots=True)
class UsageEvent:
    """C0 analytics row: a generation that reached the provider, or an inline choice."""

    id: UsageEventId
    occurred_at: datetime
    user_pseudonym: str
    scenario: UsageScenario
    surface: UsageSurface
    outcome: UsageOutcome
    unavailable_kind: str | None
    safety: str | None
    model: str | None
    prompt_version: str | None
    latency_ms: int
    ttfc_ms: int | None
    attempts: int
    input_tokens: int
    output_tokens: int
    billable_tokens: int
    event_kind: UsageEventKind = UsageEventKind.GENERATION
    variant_firmness: Firmness | None = None

    def __post_init__(self) -> None:
        require_utc(self.occurred_at)
        if self.occurred_at.microsecond != 0:
            msg = "occurred_at must be truncated to the second"
            raise InvalidValueError(msg)
        if len(self.user_pseudonym) != _HEX64 or any(
            ch not in "0123456789abcdef" for ch in self.user_pseudonym
        ):
            msg = "user_pseudonym must be 64 lowercase hex characters"
            raise InvalidValueError(msg)
        if self.latency_ms < 0 or (self.ttfc_ms is not None and self.ttfc_ms < 0):
            msg = "latencies must be non-negative"
            raise InvalidValueError(msg)
        if self.input_tokens < 0 or self.output_tokens < 0 or self.billable_tokens < 0:
            msg = "token counts must be non-negative"
            raise InvalidValueError(msg)
        if self.event_kind is UsageEventKind.GENERATION:
            self._validate_generation()
            return
        self._validate_result_chosen()

    def _validate_generation(self) -> None:
        if not self.model or not self.prompt_version:
            msg = "model and prompt_version must be non-empty"
            raise InvalidValueError(msg)
        if self.attempts < 1:
            msg = "attempts must be at least 1"
            raise InvalidValueError(msg)
        if self.variant_firmness is not None:
            msg = "generation events must not set variant_firmness"
            raise InvalidValueError(msg)

    def _validate_result_chosen(self) -> None:
        if self.variant_firmness is None:
            msg = "result_chosen events require variant_firmness"
            raise InvalidValueError(msg)
        if self.model is not None or self.prompt_version is not None:
            msg = "result_chosen events must not set model or prompt_version"
            raise InvalidValueError(msg)
        if (
            self.attempts != 0
            or self.latency_ms != 0
            or self.ttfc_ms is not None
            or self.input_tokens != 0
            or self.output_tokens != 0
            or self.billable_tokens != 0
            or self.safety is not None
            or self.unavailable_kind is not None
        ):
            msg = "result_chosen events must not carry generation metrics"
            raise InvalidValueError(msg)
        if self.outcome is not UsageOutcome.OK:
            msg = "result_chosen outcome must be ok"
            raise InvalidValueError(msg)
        if self.surface is not UsageSurface.INLINE:
            msg = "result_chosen surface must be inline"
            raise InvalidValueError(msg)
