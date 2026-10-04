"""Dispatcher-level error handler (sole documented broad failure sink)."""

from __future__ import annotations

import structlog
from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import ErrorEvent, Message

from svoi_pravila.adapters.channels.telegram.deps import TelegramDeps

logger = structlog.get_logger(__name__)


async def telegram_error_handler(event: ErrorEvent, bot: Bot, tg_deps: TelegramDeps) -> bool:
    """Log a C0 failure record and reply with a generic catalog message when possible.

    Exact location of the permitted channel failure sink (task E7):
    ``svoi_pravila.adapters.channels.telegram.errors.telegram_error_handler``.
    Aiogram delivers the failed update here; handlers themselves do not catch Exception.
    """
    update = event.update
    logger.error(
        "telegram_update_failed",
        update_type=update.event_type,
        exception_class=type(event.exception).__name__,
    )
    chat_id = _chat_id(event)
    if chat_id is not None:
        reply_error_class: str | None = None
        try:
            await bot.send_message(chat_id, tg_deps.strings.error_generic)
        except TelegramAPIError as exc:
            reply_error_class = type(exc).__name__
        if reply_error_class is not None:
            logger.error(
                "telegram_error_reply_failed",
                exception_class=reply_error_class,
            )
    return True


def _chat_id(event: ErrorEvent) -> int | None:
    update = event.update
    if update.message is not None:
        return update.message.chat.id
    callback = update.callback_query
    if callback is not None and isinstance(callback.message, Message):
        return callback.message.chat.id
    return None
