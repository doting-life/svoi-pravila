"""In-memory ExportDelivery fake."""

from __future__ import annotations

from svoi_pravila.application.errors import BotChatUnavailable
from svoi_pravila.domain.ids import TelegramUserId


class FakeExportDelivery:
    """Record delivered payloads; optionally raise ``BotChatUnavailable``."""

    def __init__(self, *, unavailable: bool = False) -> None:
        self.unavailable = unavailable
        self.deliveries: list[tuple[TelegramUserId, dict[str, object]]] = []

    async def deliver(
        self,
        telegram_user_id: TelegramUserId,
        payload: dict[str, object],
    ) -> None:
        if self.unavailable:
            raise BotChatUnavailable
        self.deliveries.append((telegram_user_id, payload))
