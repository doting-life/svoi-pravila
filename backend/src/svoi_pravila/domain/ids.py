"""Typed identifiers."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import NewType

from svoi_pravila.domain.errors import InvalidValueError

UserId = NewType("UserId", uuid.UUID)
ContactId = NewType("ContactId", uuid.UUID)
PairId = NewType("PairId", uuid.UUID)
RuleId = NewType("RuleId", uuid.UUID)
InviteId = NewType("InviteId", uuid.UUID)
ConsentId = NewType("ConsentId", uuid.UUID)
UsageEventId = NewType("UsageEventId", uuid.UUID)
RuleSuggestionId = NewType("RuleSuggestionId", uuid.UUID)


@dataclass(frozen=True, slots=True)
class TelegramUserId:
    """Positive Telegram user identifier."""

    value: int

    def __post_init__(self) -> None:
        if self.value <= 0:
            msg = "TelegramUserId must be positive"
            raise InvalidValueError(msg)
