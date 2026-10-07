"""Drop non-private message updates; allow inline and my_chat_member."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject, Update


class PrivateChatMiddleware(BaseMiddleware):
    """Ignore group/channel messages silently; pass membership and inline."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        update = event if isinstance(event, Update) else data.get("event_update")
        if not isinstance(update, Update):
            return None
        if not _is_allowed(update):
            return None
        return await handler(event, data)


def _is_allowed(update: Update) -> bool:
    if update.inline_query is not None or update.chosen_inline_result is not None:
        return True
    if update.my_chat_member is not None:
        return True
    message = update.message or update.edited_message
    if isinstance(message, Message) and message.chat is not None:
        return message.chat.type == "private"
    return False
