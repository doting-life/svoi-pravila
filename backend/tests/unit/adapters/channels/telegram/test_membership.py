"""Unit tests for membership leave-on-join handler."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram import Bot
from aiogram.enums import ChatMemberStatus, ChatType
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import LeaveChat
from aiogram.types import Chat, ChatMemberLeft, ChatMemberMember, ChatMemberUpdated, User
from tests.fakes.telegram_session import FakeTelegramSession

from svoi_pravila.adapters.channels.telegram.handlers.membership import (
    build_membership_router,
    leave_non_private,
)

_NOW = datetime(2026, 1, 1, tzinfo=UTC)
_BOT = User(id=1, is_bot=True, first_name="bot")
_HUMAN = User(id=9, is_bot=False, first_name="A")


def _member_event(
    *,
    chat_type: str,
    new_status: ChatMemberStatus,
    chat_id: int = -100,
) -> ChatMemberUpdated:
    if new_status is ChatMemberStatus.MEMBER:
        new_member: ChatMemberMember | ChatMemberLeft = ChatMemberMember(user=_BOT)
    else:
        new_member = ChatMemberLeft(user=_BOT)
    return ChatMemberUpdated(
        chat=Chat(id=chat_id, type=chat_type, title="g"),
        from_user=_HUMAN,
        date=_NOW,
        old_chat_member=ChatMemberLeft(user=_BOT),
        new_chat_member=new_member,
    )


def _status_event(
    *,
    chat_type: str,
    status: ChatMemberStatus,
    chat_id: int = -100,
) -> ChatMemberUpdated:
    event = MagicMock(spec=ChatMemberUpdated)
    event.chat = MagicMock()
    event.chat.type = chat_type
    event.chat.id = chat_id
    event.new_chat_member = MagicMock()
    event.new_chat_member.status = status
    return cast(ChatMemberUpdated, event)


@pytest.mark.unit
@pytest.mark.parametrize("chat_type", ["group", "supergroup", "channel"])
@pytest.mark.parametrize(
    "status",
    [ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.RESTRICTED],
)
async def test_leave_non_private_on_join(chat_type: str, status: ChatMemberStatus) -> None:
    session = FakeTelegramSession()
    bot = Bot(token="1:TEST", session=session)
    if status is ChatMemberStatus.MEMBER:
        event = _member_event(chat_type=chat_type, new_status=status)
    else:
        event = _status_event(chat_type=chat_type, status=status)
    await leave_non_private(event, bot)
    leaves = [req for req in session.requests if isinstance(req, LeaveChat)]
    assert len(leaves) == 1
    assert leaves[0].chat_id == -100


@pytest.mark.unit
async def test_leave_skips_private_and_left_status() -> None:
    session = FakeTelegramSession()
    bot = Bot(token="1:TEST", session=session)
    await leave_non_private(
        _member_event(chat_type=ChatType.PRIVATE, new_status=ChatMemberStatus.MEMBER, chat_id=5),
        bot,
    )
    await leave_non_private(
        _member_event(chat_type=ChatType.GROUP, new_status=ChatMemberStatus.LEFT),
        bot,
    )
    await leave_non_private(
        _status_event(chat_type=ChatType.GROUP, status=ChatMemberStatus.KICKED),
        bot,
    )
    assert session.requests == []


@pytest.mark.unit
async def test_leave_chat_telegram_error_is_logged(
    capture_log_events: Callable[[], list[dict[str, Any]]],
) -> None:
    session = FakeTelegramSession()
    bot = Bot(token="1:TEST", session=session)
    failing = AsyncMock(
        side_effect=TelegramBadRequest(method=LeaveChat(chat_id=-100), message="fail")
    )
    object.__setattr__(bot, "leave_chat", failing)
    await leave_non_private(
        _member_event(chat_type="group", new_status=ChatMemberStatus.MEMBER),
        bot,
    )
    events = capture_log_events()
    assert any(event.get("event") == "telegram_leave_chat_failed" for event in events)


@pytest.mark.unit
def test_build_membership_router_registers_handler() -> None:
    router = build_membership_router()
    assert router.my_chat_member.handlers
    assert router.my_chat_member.handlers[0].callback is leave_non_private
