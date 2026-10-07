"""Port for Telegram Mini App initData verification."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from svoi_pravila.application.errors import ApplicationError
from svoi_pravila.domain.ids import TelegramUserId


class InitDataInvalid(ApplicationError):
    """initData failed signature or structural checks (C0)."""


class InitDataExpired(ApplicationError):
    """initData auth_date is outside the allowed freshness window (C0)."""


@dataclass(frozen=True, slots=True)
class VerifiedInitData:
    """Boundary DTO: Telegram id, auth_date, and optional start_param (C0/C1)."""

    telegram_user_id: TelegramUserId
    auth_date: datetime
    start_param: str | None = None


class InitDataVerifier(Protocol):
    """Verify raw WebApp initData and drop all other fields."""

    def verify(self, raw: str) -> VerifiedInitData:
        """Return verified ids or raise InitDataInvalid / InitDataExpired."""
        ...
