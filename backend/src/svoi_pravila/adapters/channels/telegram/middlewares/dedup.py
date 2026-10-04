"""Deduplicate Telegram updates by update_id."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Update

from svoi_pravila.adapters.channels.telegram.deps import TelegramDeps


class DedupMiddleware(BaseMiddleware):
    """Claim each update_id once via the UpdateDeduplicator port."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        deps: TelegramDeps = data["tg_deps"]
        update = event if isinstance(event, Update) else data.get("event_update")
        if not isinstance(update, Update):
            return None
        claimed = await deps.deduplicator.claim(update.update_id)
        if not claimed:
            return None
        return await handler(event, data)
