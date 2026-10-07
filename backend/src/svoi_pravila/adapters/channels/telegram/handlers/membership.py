"""Leave groups and channels when the bot is added."""

from __future__ import annotations

import structlog
from aiogram import Bot, Router
from aiogram.enums import ChatMemberStatus, ChatType
from aiogram.exceptions import TelegramAPIError
from aiogram.types import ChatMemberUpdated

logger = structlog.get_logger(__name__)

_JOIN_STATUSES = frozenset(
    {
        ChatMemberStatus.MEMBER,
        ChatMemberStatus.ADMINISTRATOR,
        ChatMemberStatus.RESTRICTED,
    }
)
_GROUP_TYPES = frozenset({ChatType.GROUP, ChatType.SUPERGROUP, ChatType.CHANNEL})


def build_membership_router() -> Router:
    """Register my_chat_member handler that leaves non-private chats."""
    router = Router(name="telegram_membership")
    router.my_chat_member.register(leave_non_private)
    return router


async def leave_non_private(event: ChatMemberUpdated, bot: Bot) -> None:
    """Leave when the bot becomes a member of a group, supergroup, or channel."""
    chat = event.chat
    if chat.type not in _GROUP_TYPES:
        return
    new_status = event.new_chat_member.status
    if new_status not in _JOIN_STATUSES:
        return
    try:
        await bot.leave_chat(chat.id)
    except TelegramAPIError as exc:
        logger.info(
            "telegram_leave_chat_failed",
            error_type=type(exc).__name__,
            chat_type=chat.type,
        )
