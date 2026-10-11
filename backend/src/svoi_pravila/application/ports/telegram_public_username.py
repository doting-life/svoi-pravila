"""Port for a request-scoped Telegram public username lookup."""

from __future__ import annotations

from typing import Protocol

from svoi_pravila.domain.ids import TelegramUserId


class TelegramPublicUsername(Protocol):
    """Resolve a public @username via Bot API ``getChat`` (no storage)."""

    async def username_for(self, telegram_user_id: TelegramUserId) -> str | None:
        """Return the public username without ``@``, or ``None`` when absent/failed."""
