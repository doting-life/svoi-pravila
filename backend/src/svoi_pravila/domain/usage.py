"""C0 usage-event record (no conversation text, no Telegram identifiers)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from svoi_pravila.domain.enums import UsageOutcome, UsageScenario, UsageSurface
from svoi_pravila.domain.errors import InvalidValueError
from svoi_pravila.domain.ids import UsageEventId
from svoi_pravila.domain.time import require_utc

_HEX64 = 64


@dataclass(frozen=True, slots=True)
class UsageEvent:
    """One generation attempt that reached the text generator."""

    id: UsageEventId
    occurred_at: datetime
    user_pseudonym: str
    scenario: UsageScenario
    surface: UsageSurface
    outcome: UsageOutcome
    unavailable_kind: str | None
    safety: str | None
    model: str
    prompt_version: str
    latency_ms: int
    ttfc_ms: int | None
    attempts: int
    input_tokens: int
    output_tokens: int
    billable_tokens: int

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
        if not self.model or not self.prompt_version:
            msg = "model and prompt_version must be non-empty"
            raise InvalidValueError(msg)
        if self.latency_ms < 0 or (self.ttfc_ms is not None and self.ttfc_ms < 0):
            msg = "latencies must be non-negative"
            raise InvalidValueError(msg)
        if self.attempts < 1:
            msg = "attempts must be at least 1"
            raise InvalidValueError(msg)
        if self.input_tokens < 0 or self.output_tokens < 0 or self.billable_tokens < 0:
            msg = "token counts must be non-negative"
            raise InvalidValueError(msg)
