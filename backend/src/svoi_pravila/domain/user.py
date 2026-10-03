"""User aggregate."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime

from svoi_pravila.domain.errors import InvalidTransitionError, InvalidValueError
from svoi_pravila.domain.ids import ContactId, TelegramUserId, UserId
from svoi_pravila.domain.time import require_utc


@dataclass(frozen=True, slots=True)
class User:
    """Channel user with optional age confirmation and active contact."""

    id: UserId
    telegram_user_id: TelegramUserId
    created_at: datetime
    age_confirmed_at: datetime | None
    active_contact_id: ContactId | None

    def __post_init__(self) -> None:
        require_utc(self.created_at)
        if self.age_confirmed_at is not None:
            require_utc(self.age_confirmed_at)
            if self.age_confirmed_at < self.created_at:
                msg = "age_confirmed_at must be at or after created_at"
                raise InvalidValueError(msg)

    def confirm_age(self, now: datetime) -> User:
        """Confirm age; idempotent — keeps the first confirmation timestamp."""
        require_utc(now)
        if now < self.created_at:
            msg = "confirm_age time must be at or after created_at"
            raise InvalidTransitionError(msg)
        if self.age_confirmed_at is not None:
            return self
        return replace(self, age_confirmed_at=now)

    def set_active_contact(self, contact_id: ContactId) -> User:
        """Set the active contact for scenarios."""
        return replace(self, active_contact_id=contact_id)

    def clear_active_contact(self) -> User:
        """Clear the active contact."""
        return replace(self, active_contact_id=None)
