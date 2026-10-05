"""RuleSuggestion aggregate and ToneSignal value for D-6 tone candidates."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime

from svoi_pravila.domain.enums import (
    Firmness,
    RuleCategory,
    SuggestionSource,
    SuggestionStatus,
)
from svoi_pravila.domain.errors import InvalidTransitionError, InvalidValueError
from svoi_pravila.domain.ids import ContactId, RuleSuggestionId, UserId
from svoi_pravila.domain.text import RuleText
from svoi_pravila.domain.time import require_utc

TONE_SIGNAL_WINDOW = 10
TONE_SIGNAL_MIN_SAMPLES = 5
TONE_SIGNAL_DOMINANCE = 0.8


def _require(*, ok: bool, message: str) -> None:
    if not ok:
        raise InvalidValueError(message)


@dataclass(frozen=True, slots=True)
class ToneSignal:
    """Last ≤ ``TONE_SIGNAL_WINDOW`` firmness choices for one (user, contact)."""

    user_id: UserId
    contact_id: ContactId
    values: tuple[Firmness, ...]

    def __post_init__(self) -> None:
        _require(
            ok=len(self.values) <= TONE_SIGNAL_WINDOW,
            message=f"ToneSignal holds at most {TONE_SIGNAL_WINDOW} values",
        )

    def append(self, firmness: Firmness) -> ToneSignal:
        """Append a firmness choice, dropping the oldest when over the window."""
        next_values = (*self.values, firmness)
        if len(next_values) > TONE_SIGNAL_WINDOW:
            next_values = next_values[-TONE_SIGNAL_WINDOW:]
        return replace(self, values=next_values)


def dominant_firmness(signal: ToneSignal) -> Firmness | None:
    """Return the dominant firmness when the sample and share thresholds are met."""
    if len(signal.values) < TONE_SIGNAL_MIN_SAMPLES:
        return None
    counts: dict[Firmness, int] = {}
    for value in signal.values:
        counts[value] = counts.get(value, 0) + 1
    total = len(signal.values)
    winners = [
        firmness for firmness, count in counts.items() if count / total >= TONE_SIGNAL_DOMINANCE
    ]
    if len(winners) != 1:
        return None
    return winners[0]


@dataclass(frozen=True, slots=True)
class RuleSuggestion:
    """System-proposed rule candidate; becomes a rule only after user confirm (D-6)."""

    id: RuleSuggestionId
    user_id: UserId
    contact_id: ContactId
    source: SuggestionSource
    category: RuleCategory
    text: RuleText
    firmness: Firmness | None
    status: SuggestionStatus
    created_at: datetime
    decided_at: datetime | None

    def __post_init__(self) -> None:
        require_utc(self.created_at)
        if self.decided_at is not None:
            require_utc(self.decided_at)
        if self.source is SuggestionSource.TONE:
            _require(ok=self.firmness is not None, message="tone suggestion requires firmness")
        else:
            _require(ok=self.firmness is None, message="decode suggestion forbids firmness")
        if self.status is SuggestionStatus.PENDING:
            _require(ok=self.decided_at is None, message="pending suggestion has no decided_at")
        else:
            _require(
                ok=self.decided_at is not None, message="decided suggestion requires decided_at"
            )

    @classmethod
    def create_tone(
        cls,
        *,
        suggestion_id: RuleSuggestionId,
        user_id: UserId,
        contact_id: ContactId,
        category: RuleCategory,
        text: RuleText,
        firmness: Firmness,
        now: datetime,
    ) -> RuleSuggestion:
        """Create a pending tone-source suggestion."""
        return cls(
            id=suggestion_id,
            user_id=user_id,
            contact_id=contact_id,
            source=SuggestionSource.TONE,
            category=category,
            text=text,
            firmness=firmness,
            status=SuggestionStatus.PENDING,
            created_at=now,
            decided_at=None,
        )

    @classmethod
    def create_decode(
        cls,
        *,
        suggestion_id: RuleSuggestionId,
        user_id: UserId,
        contact_id: ContactId,
        category: RuleCategory,
        text: RuleText,
        now: datetime,
    ) -> RuleSuggestion:
        """Create a pending decode-source suggestion."""
        return cls(
            id=suggestion_id,
            user_id=user_id,
            contact_id=contact_id,
            source=SuggestionSource.DECODE,
            category=category,
            text=text,
            firmness=None,
            status=SuggestionStatus.PENDING,
            created_at=now,
            decided_at=None,
        )

    def accept(self, now: datetime) -> RuleSuggestion:
        """Transition pending → accepted."""
        if self.status is not SuggestionStatus.PENDING:
            msg = "suggestion already decided"
            raise InvalidTransitionError(msg)
        require_utc(now)
        return replace(self, status=SuggestionStatus.ACCEPTED, decided_at=now)

    def dismiss(self, now: datetime) -> RuleSuggestion:
        """Transition pending → dismissed."""
        if self.status is not SuggestionStatus.PENDING:
            msg = "suggestion already decided"
            raise InvalidTransitionError(msg)
        require_utc(now)
        return replace(self, status=SuggestionStatus.DISMISSED, decided_at=now)
