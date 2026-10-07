"""Integration privacy canaries for closed-DM welcome and membership."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import pytest
from aiogram import Bot
from aiogram.methods import LeaveChat, SendMessage
from aiogram.types import (
    Chat,
    ChatMemberLeft,
    ChatMemberMember,
    ChatMemberUpdated,
    Message,
    MessageEntity,
    Update,
    User,
)
from redis.asyncio import Redis

from svoi_pravila.adapters.cache.client import close_client, create_client
from svoi_pravila.adapters.cache.welcome_throttle import ValkeyWelcomeThrottle
from svoi_pravila.adapters.channels.telegram.factory import build_telegram_lifecycle
from svoi_pravila.config import Environment, Settings, TelegramUpdatesMode
from tests.factories import make_settings
from tests.fakes.telegram_deps import TelegramTestDeps, make_telegram_deps
from tests.fakes.telegram_session import FakeTelegramSession

_SENTINEL_TEXT = "SENTINEL_TEXT_PRIVACY_0025_INT"
_SENTINEL_FIRST = "SENTINEL_FIRST_PRIVACY_0025_INT"
_SENTINEL_LAST = "SENTINEL_LAST_PRIVACY_0025_INT"
_SENTINEL_USER = "sentinel_user_privacy_0025_int"
_SENTINEL_ID = 9876543210888
_NOW = datetime(2026, 1, 1, tzinfo=UTC)
_MINIAPP = "https://miniapp.example"


def _db15_url(valkey_url: str) -> str:
    parts = urlsplit(valkey_url)
    return urlunsplit((parts.scheme, parts.netloc, "/15", parts.query, parts.fragment))


@pytest.fixture
async def valkey_db15(settings: Settings) -> AsyncIterator[Redis]:
    client = create_client(
        make_settings(
            database_url=settings.database_url.get_secret_value(),
            valkey_url=_db15_url(settings.valkey_url.get_secret_value()),
        )
    )
    await client.flushdb()
    try:
        yield client
    finally:
        await client.flushdb()
        await close_client(client)


def _assert_no_markers(blob: str) -> None:
    for marker in (
        _SENTINEL_TEXT,
        _SENTINEL_FIRST,
        _SENTINEL_LAST,
        _SENTINEL_USER,
        str(_SENTINEL_ID),
    ):
        assert marker not in blob


@pytest.mark.integration
async def test_welcome_throttled_and_privacy_canary(
    settings: Settings,
    valkey_db15: Redis,
    capture_log_events: Callable[[], list[dict[str, Any]]],
) -> None:
    deps = make_telegram_deps(
        TelegramTestDeps(
            welcome=ValkeyWelcomeThrottle(valkey_db15),
            miniapp_url=_MINIAPP,
        )
    )
    session = FakeTelegramSession()
    bot = Bot(token="1:TEST", session=session)
    lifecycle = build_telegram_lifecycle(
        make_settings(
            environment=Environment.LOCAL,
            telegram_updates_mode=TelegramUpdatesMode.POLLING,
            telegram_bot_token="1:TEST",
            database_url=settings.database_url.get_secret_value(),
            valkey_url=settings.valkey_url.get_secret_value(),
            miniapp_url=_MINIAPP,
        ),
        deps,
        bot=bot,
    )
    origin = User(
        id=_SENTINEL_ID,
        is_bot=False,
        first_name=_SENTINEL_FIRST,
        last_name=_SENTINEL_LAST,
        username=_SENTINEL_USER,
    )
    await lifecycle.dispatcher.feed_update(
        bot,
        Update(
            update_id=1,
            message=Message(
                message_id=1,
                date=_NOW,
                chat=Chat(id=_SENTINEL_ID, type="private"),
                from_user=origin,
                text=_SENTINEL_TEXT,
                caption=_SENTINEL_TEXT,
                entities=[MessageEntity(type="bold", offset=0, length=5)],
            ),
        ),
    )
    sends = [req for req in session.requests if isinstance(req, SendMessage)]
    assert len(sends) == 1
    assert sends[0].text == deps.strings.dm_welcome

    await lifecycle.dispatcher.feed_update(
        bot,
        Update(
            update_id=2,
            message=Message(
                message_id=2,
                date=_NOW,
                chat=Chat(id=_SENTINEL_ID, type="private"),
                from_user=origin,
                text=_SENTINEL_TEXT,
            ),
        ),
    )
    assert len([req for req in session.requests if isinstance(req, SendMessage)]) == 1

    events = capture_log_events()
    blob = json.dumps(events) + "\n".join(str(event) for event in events)
    _assert_no_markers(blob)
    keys = [key async for key in valkey_db15.scan_iter(match="tg:welcome:*")]
    assert len(keys) == 1
    assert str(_SENTINEL_ID) not in keys[0]


@pytest.mark.integration
async def test_membership_leave_chat_on_group_join(settings: Settings) -> None:
    deps = make_telegram_deps(TelegramTestDeps(miniapp_url=_MINIAPP))
    session = FakeTelegramSession()
    bot = Bot(token="1:TEST", session=session)
    lifecycle = build_telegram_lifecycle(
        make_settings(
            environment=Environment.LOCAL,
            telegram_updates_mode=TelegramUpdatesMode.POLLING,
            telegram_bot_token="1:TEST",
            database_url=settings.database_url.get_secret_value(),
            valkey_url=settings.valkey_url.get_secret_value(),
            miniapp_url=_MINIAPP,
        ),
        deps,
        bot=bot,
    )
    bot_user = User(id=1, is_bot=True, first_name="bot")
    await lifecycle.dispatcher.feed_update(
        bot,
        Update(
            update_id=10,
            my_chat_member=ChatMemberUpdated(
                chat=Chat(id=-100123, type="supergroup", title="g"),
                from_user=User(id=9, is_bot=False, first_name="A"),
                date=_NOW,
                old_chat_member=ChatMemberLeft(user=bot_user),
                new_chat_member=ChatMemberMember(user=bot_user),
            ),
        ),
    )
    leaves = [req for req in session.requests if isinstance(req, LeaveChat)]
    assert len(leaves) == 1
    assert leaves[0].chat_id == -100123
