"""Rate-limit inline updates by HMAC pseudonym; private messages skip the limiter."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Update

from svoi_pravila.adapters.channels.telegram.deps import TelegramDeps

_PURPOSE = "rate_limit"


class RateLimitMiddleware(BaseMiddleware):
    """Apply the fixed-window limit to inline updates only."""

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
        if update.my_chat_member is not None:
            return await handler(event, data)
        if update.message is not None or update.edited_message is not None:
            return await handler(event, data)
        user_id = _telegram_user_id(update)
        if user_id is None:
            return None
        pseudonym = deps.pseudonymizer.pseudonymize(_PURPOSE, str(user_id))
        decision = await deps.rate_limiter.check(pseudonym)
        if decision.allowed:
            return await handler(event, data)
        return None


def _telegram_user_id(update: Update) -> int | None:
    if update.inline_query and update.inline_query.from_user:
        return update.inline_query.from_user.id
    if update.chosen_inline_result and update.chosen_inline_result.from_user:
        return update.chosen_inline_result.from_user.id
    return None
