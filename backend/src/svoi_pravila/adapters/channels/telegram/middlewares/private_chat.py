"""Drop updates that are not from a private chat."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject, Update


class PrivateChatMiddleware(BaseMiddleware):
    """Ignore group/channel updates silently."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        update = event if isinstance(event, Update) else data.get("event_update")
        if not isinstance(update, Update):
            return None
        if not _is_private(update):
            return None
        return await handler(event, data)


def _is_private(update: Update) -> bool:
    message = update.message or update.edited_message
    if isinstance(message, Message) and message.chat is not None:
        return message.chat.type == "private"
    callback = update.callback_query
    if isinstance(callback, CallbackQuery) and callback.message is not None:
        return callback.message.chat.type == "private"
    return False
