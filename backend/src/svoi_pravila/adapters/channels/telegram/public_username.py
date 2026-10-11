"""Bot API ``getChat`` adapter for a request-scoped public username."""

from __future__ import annotations

import structlog
from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

from svoi_pravila.domain.ids import TelegramUserId

logger = structlog.get_logger(__name__)


class AiogramTelegramPublicUsername:
    """Resolve usernames via ``getChat``; never cache or log ids/usernames."""

    def __init__(self, bot: Bot) -> None:
        self._bot = bot

    async def username_for(self, telegram_user_id: TelegramUserId) -> str | None:
        """Return the public username without ``@``, or ``None`` on miss/failure."""
        try:
            chat = await self._bot.get_chat(telegram_user_id.value)
        except TelegramAPIError as exc:
            logger.info(
                "partner_username_lookup_failed",
                exception_class=type(exc).__name__,
            )
            return None
        username = chat.username
        if username is None or username == "":
            return None
        return username.lstrip("@")
