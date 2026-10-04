"""Rate-limit private-chat updates by HMAC pseudonym."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware, Bot
from aiogram.types import CallbackQuery, Message, TelegramObject, Update

from svoi_pravila.adapters.channels.telegram.deps import TelegramDeps

_PURPOSE = "rate_limit"


class RateLimitMiddleware(BaseMiddleware):
    """Notify once per window when the fixed-window limit is exceeded."""

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
        user_id = _telegram_user_id(update)
        chat_id = _chat_id(update)
        if user_id is None:
            return None
        pseudonym = deps.pseudonymizer.pseudonymize(_PURPOSE, str(user_id))
        decision = await deps.rate_limiter.check(pseudonym)
        if decision.allowed:
            return await handler(event, data)
        if decision.first_rejection and chat_id is not None:
            bot: Bot = data["bot"]
            await bot.send_message(chat_id, deps.strings.rate_limited)
        return None


def _telegram_user_id(update: Update) -> int | None:
    if update.message and update.message.from_user:
        return update.message.from_user.id
    if update.callback_query and update.callback_query.from_user:
        return update.callback_query.from_user.id
    return None


def _chat_id(update: Update) -> int | None:
    message = update.message
    if isinstance(message, Message):
        return message.chat.id
    callback = update.callback_query
    if isinstance(callback, CallbackQuery) and callback.message is not None:
        return callback.message.chat.id
    return None
