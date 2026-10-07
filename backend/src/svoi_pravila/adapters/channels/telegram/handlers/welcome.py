"""Closed private-chat handler: static welcome with web_app, throttled."""

from __future__ import annotations

import structlog
from aiogram import Bot, Router
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message, WebAppInfo

from svoi_pravila.adapters.channels.telegram.deps import TelegramDeps
from svoi_pravila.application.ports.welcome_throttle import WELCOME_THROTTLE_PURPOSE

logger = structlog.get_logger(__name__)


def build_welcome_router() -> Router:
    """Register the single private-message handler for all content types."""
    router = Router(name="telegram_welcome")
    router.message.register(private_welcome)
    return router


async def private_welcome(message: Message, tg_deps: TelegramDeps, bot: Bot) -> None:
    """Reply once per 10 minutes with a static welcome; never read message content."""
    if message.from_user is None:
        return
    if message.chat is None or message.chat.type != "private":
        return
    user_id = message.from_user.id
    chat_id = message.chat.id
    pseudonym = tg_deps.pseudonymizer.pseudonymize(WELCOME_THROTTLE_PURPOSE, str(user_id))
    claimed = await tg_deps.welcome_throttle.claim(pseudonym)
    if not claimed:
        return
    miniapp_url = tg_deps.miniapp_url
    if miniapp_url is None:
        logger.warning("telegram_welcome_skipped_no_miniapp_url")
        return
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=tg_deps.strings.dm_open_app,
                    web_app=WebAppInfo(url=miniapp_url),
                )
            ]
        ]
    )
    await bot.send_message(chat_id, tg_deps.strings.dm_welcome, reply_markup=keyboard)
