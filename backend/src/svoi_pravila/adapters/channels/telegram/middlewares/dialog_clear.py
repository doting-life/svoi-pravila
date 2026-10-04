"""Clear ephemeral dialog state when the user sends any bot command."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject, Update

from svoi_pravila.adapters.channels.telegram.deps import TelegramDeps
from svoi_pravila.application.ports.dialog_state import DIALOG_PSEUDONYM_PURPOSE


class DialogClearMiddleware(BaseMiddleware):
    """Any private-chat command clears dialog; callbacks are left untouched."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        deps: TelegramDeps = data["tg_deps"]
        update = event if isinstance(event, Update) else data.get("event_update")
        if isinstance(update, Update):
            message = update.message
            if (
                isinstance(message, Message)
                and message.from_user is not None
                and message.text is not None
                and message.text.startswith("/")
            ):
                pseudonym = deps.pseudonymizer.pseudonymize(
                    DIALOG_PSEUDONYM_PURPOSE, str(message.from_user.id)
                )
                await deps.dialog_state.clear(pseudonym)
        return await handler(event, data)
