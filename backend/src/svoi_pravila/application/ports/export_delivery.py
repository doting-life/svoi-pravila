"""Port for delivering a user data export to the user's bot chat."""

from __future__ import annotations

from typing import Protocol

from svoi_pravila.domain.ids import TelegramUserId


class ExportDelivery(Protocol):
    """Send an in-memory export payload to the user's private bot chat."""

    async def deliver(
        self,
        telegram_user_id: TelegramUserId,
        payload: dict[str, object],
    ) -> None:
        """Deliver ``payload`` as a document. Raises ``BotChatUnavailable`` if undeliverable."""
        ...
