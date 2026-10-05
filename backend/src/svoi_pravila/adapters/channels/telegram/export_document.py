"""Shared helper to send a user-data export JSON document via Telegram."""

from __future__ import annotations

import json
from datetime import datetime

import structlog
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import BufferedInputFile

from svoi_pravila.application.errors import BotChatUnavailable
from svoi_pravila.application.ports.clock import Clock
from svoi_pravila.domain.ids import TelegramUserId

logger = structlog.get_logger(__name__)


async def send_export_document(
    bot: Bot,
    *,
    chat_id: int,
    payload: dict[str, object],
    now: datetime,
    caption: str,
) -> None:
    """Serialize ``payload`` and ``send_document`` to ``chat_id``.

    Raises ``BotChatUnavailable`` when the user blocked the bot or never started it.
    """
    body = json.dumps(payload, ensure_ascii=False, indent=2)
    stamp = now.strftime("%Y%m%d")
    document = BufferedInputFile(
        body.encode("utf-8"),
        filename=f"svoi-pravila-export-{stamp}.json",
    )
    try:
        await bot.send_document(
            chat_id=chat_id,
            document=document,
            caption=caption,
        )
    except (TelegramForbiddenError, TelegramBadRequest) as exc:
        logger.info("export_delivery_failed", error_type=type(exc).__name__)
        raise BotChatUnavailable from exc


class TelegramExportDelivery:
    """``ExportDelivery`` that sends the JSON dump to the user's private chat."""

    def __init__(self, bot: Bot, *, clock: Clock, caption: str) -> None:
        self._bot = bot
        self._clock = clock
        self._caption = caption

    async def deliver(
        self,
        telegram_user_id: TelegramUserId,
        payload: dict[str, object],
    ) -> None:
        """Deliver ``payload`` to ``telegram_user_id``'s private chat."""
        await send_export_document(
            self._bot,
            chat_id=telegram_user_id.value,
            payload=payload,
            now=self._clock.now(),
            caption=self._caption,
        )
