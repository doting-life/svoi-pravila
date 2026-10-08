"""Telegram channel middleware, welcome, and lifecycle tests."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.methods import (
    AnswerInlineQuery,
    DeleteMyCommands,
    DeleteWebhook,
    SendMessage,
    SetChatMenuButton,
    SetWebhook,
)
from aiogram.types import (
    Chat,
    InlineQuery,
    MenuButtonWebApp,
    Message,
    PhotoSize,
    Update,
    User,
)
from tests.factories import make_settings
from tests.fakes.consent_catalog import FakeConsentCatalog
from tests.fakes.telegram_deps import TelegramTestDeps, make_telegram_deps
from tests.fakes.telegram_session import FakeTelegramSession
from tests.fakes.uow import InMemoryUnitOfWorkFactory

from svoi_pravila.adapters.channels.telegram.deps import TelegramDeps
from svoi_pravila.adapters.channels.telegram.factory import build_telegram_lifecycle
from svoi_pravila.config import Environment, TelegramUpdatesMode
from svoi_pravila.domain.ids import TelegramUserId

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _world(
    *, rate_limit: int = 30
) -> tuple[TelegramDeps, InMemoryUnitOfWorkFactory, FakeConsentCatalog]:
    uow = InMemoryUnitOfWorkFactory()
    catalog = FakeConsentCatalog()
    deps = make_telegram_deps(TelegramTestDeps(uow=uow, catalog=catalog, rate_limit=rate_limit))
    return deps, uow, catalog


def _private_message(update_id: int, user_id: int, text: str) -> Update:
    return Update(
        update_id=update_id,
        message=Message(
            message_id=1,
            date=_NOW,
            chat=Chat(id=user_id, type="private"),
            from_user=User(id=user_id, is_bot=False, first_name="A"),
            text=text,
        ),
    )


def _inline(update_id: int, user_id: int, query: str) -> Update:
    return Update(
        update_id=update_id,
        inline_query=InlineQuery(
            id=str(update_id),
            from_user=User(id=user_id, is_bot=False, first_name="A"),
            query=query,
            offset="",
        ),
    )


def _sent_texts(session: FakeTelegramSession) -> list[str]:
    return [str(req.text) for req in session.requests if isinstance(req, SendMessage)]


@pytest.mark.unit
async def test_polling_startup_calls() -> None:
    deps, _uow, _catalog = _world()
    session = FakeTelegramSession()
    settings = make_settings(
        environment=Environment.LOCAL,
        telegram_updates_mode=TelegramUpdatesMode.POLLING,
        telegram_bot_token="1:TEST",
    )
    bot = Bot(token="1:TEST", session=session)
    lifecycle = build_telegram_lifecycle(settings, deps, bot=bot)
    await lifecycle.start()
    await lifecycle.shutdown()
    kinds = [type(req) for req in session.requests]
    assert kinds.count(DeleteMyCommands) == 4
    assert DeleteWebhook in kinds
    menu_calls = [req for req in session.requests if isinstance(req, SetChatMenuButton)]
    assert len(menu_calls) == 1
    button = menu_calls[0].menu_button
    assert isinstance(button, MenuButtonWebApp)
    assert button.text == "Мои правила"
    assert button.web_app.url == settings.miniapp_url
    assert session.closed is True
    assert [type(req) for req in session.requests].count(DeleteMyCommands) == 4


@pytest.mark.unit
async def test_menu_button_telegram_failure_does_not_stop_startup() -> None:
    deps, _uow, _catalog = _world()
    session = FakeTelegramSession()
    session.set_error(
        SetChatMenuButton,
        TelegramAPIError(method=SetChatMenuButton(), message="boom"),
    )
    settings = make_settings(
        environment=Environment.LOCAL,
        telegram_updates_mode=TelegramUpdatesMode.POLLING,
        telegram_bot_token="1:TEST",
        miniapp_url="https://example.trycloudflare.com",
    )
    bot = Bot(token="1:TEST", session=session)
    lifecycle = build_telegram_lifecycle(settings, deps, bot=bot)
    await lifecycle.start()
    await lifecycle.shutdown()
    kinds = [type(req) for req in session.requests]
    assert kinds.count(DeleteMyCommands) == 4
    assert DeleteWebhook in kinds
    assert session.closed is True


@pytest.mark.unit
async def test_webhook_startup_calls() -> None:
    deps, _uow, _catalog = _world()
    session = FakeTelegramSession()
    settings = make_settings(
        environment=Environment.LOCAL,
        telegram_updates_mode=TelegramUpdatesMode.WEBHOOK,
        telegram_bot_token="1:TEST",
        telegram_webhook_base_url="https://example.example",
        telegram_webhook_path_secret="p" * 32,
        telegram_webhook_secret_token="s" * 32,
    )
    bot = Bot(token="1:TEST", session=session)
    lifecycle = build_telegram_lifecycle(settings, deps, bot=bot)
    await lifecycle.start()
    await lifecycle.shutdown()
    kinds = [type(req) for req in session.requests]
    assert SetWebhook in kinds
    registered = [req for req in session.requests if isinstance(req, SetWebhook)]
    assert [req.url for req in registered] == [f"https://example.example/tg/{'p' * 32}"]
    assert [req.secret_token for req in registered] == ["s" * 32]


@pytest.mark.unit
async def test_group_chat_ignored() -> None:
    deps, uow, _catalog = _world()
    session = FakeTelegramSession()
    settings = make_settings(
        environment=Environment.LOCAL,
        telegram_updates_mode=TelegramUpdatesMode.POLLING,
        telegram_bot_token="1:TEST",
    )
    bot = Bot(token="1:TEST", session=session)
    lifecycle = build_telegram_lifecycle(settings, deps, bot=bot)
    update = Update(
        update_id=1,
        message=Message(
            message_id=1,
            date=_NOW,
            chat=Chat(id=-100, type="group", title="g"),
            from_user=User(id=9, is_bot=False, first_name="A"),
            text="/start",
        ),
    )
    await lifecycle.dispatcher.feed_update(bot, update)
    assert _sent_texts(session) == []
    async with uow() as unit:
        assert await unit.users.get_by_telegram_id(TelegramUserId(9)) is None


@pytest.mark.unit
async def test_dedup_drops_second_delivery() -> None:
    deps, _uow, _catalog = _world()
    session = FakeTelegramSession()
    settings = make_settings(
        environment=Environment.LOCAL,
        telegram_updates_mode=TelegramUpdatesMode.POLLING,
        telegram_bot_token="1:TEST",
    )
    bot = Bot(token="1:TEST", session=session)
    lifecycle = build_telegram_lifecycle(settings, deps, bot=bot)
    update = _private_message(10, 11, "/start")
    await lifecycle.dispatcher.feed_update(bot, update)
    first_count = len(_sent_texts(session))
    assert first_count == 1
    assert deps.strings.dm_welcome in _sent_texts(session)
    await lifecycle.dispatcher.feed_update(bot, update)
    assert len(_sent_texts(session)) == first_count


@pytest.mark.unit
async def test_welcome_throttles_second_private_message() -> None:
    deps, _uow, _catalog = _world()
    session = FakeTelegramSession()
    settings = make_settings(
        environment=Environment.LOCAL,
        telegram_updates_mode=TelegramUpdatesMode.POLLING,
        telegram_bot_token="1:TEST",
    )
    bot = Bot(token="1:TEST", session=session)
    lifecycle = build_telegram_lifecycle(settings, deps, bot=bot)
    await lifecycle.dispatcher.feed_update(bot, _private_message(1, 20, "hello"))
    await lifecycle.dispatcher.feed_update(bot, _private_message(2, 20, "again"))
    assert _sent_texts(session) == [deps.strings.dm_welcome]


@pytest.mark.unit
async def test_non_text_private_message_gets_welcome() -> None:
    deps, _uow, _catalog = _world()
    session = FakeTelegramSession()
    settings = make_settings(
        environment=Environment.LOCAL,
        telegram_updates_mode=TelegramUpdatesMode.POLLING,
        telegram_bot_token="1:TEST",
    )
    bot = Bot(token="1:TEST", session=session)
    lifecycle = build_telegram_lifecycle(settings, deps, bot=bot)
    update = Update(
        update_id=100,
        message=Message(
            message_id=1,
            date=_NOW,
            chat=Chat(id=77, type="private"),
            from_user=User(id=77, is_bot=False, first_name="A"),
            photo=[
                PhotoSize(file_id="x", file_unique_id="y", width=1, height=1),
            ],
        ),
    )
    await lifecycle.dispatcher.feed_update(bot, update)
    assert _sent_texts(session) == [deps.strings.dm_welcome]
    sends = [req for req in session.requests if isinstance(req, SendMessage)]
    assert sends[0].reply_markup is not None


@pytest.mark.unit
async def test_rate_limit_silently_drops_inline() -> None:
    deps, _uow, _catalog = _world(rate_limit=2)
    session = FakeTelegramSession()
    settings = make_settings(
        environment=Environment.LOCAL,
        telegram_updates_mode=TelegramUpdatesMode.POLLING,
        telegram_bot_token="1:TEST",
    )
    bot = Bot(token="1:TEST", session=session)
    lifecycle = build_telegram_lifecycle(settings, deps, bot=bot)
    for update_id in (1, 2, 3, 4):
        await lifecycle.dispatcher.feed_update(bot, _inline(update_id, 20, "long enough draft"))
        pending = [task for task in deps.inline_queries.tasks if not task.done()]
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
    answers = [req for req in session.requests if isinstance(req, AnswerInlineQuery)]
    assert len(answers) == 2
    assert _sent_texts(session) == []


@pytest.mark.unit
async def test_private_messages_bypass_rate_limit() -> None:
    deps, _uow, _catalog = _world(rate_limit=1)
    session = FakeTelegramSession()
    settings = make_settings(
        environment=Environment.LOCAL,
        telegram_updates_mode=TelegramUpdatesMode.POLLING,
        telegram_bot_token="1:TEST",
    )
    bot = Bot(token="1:TEST", session=session)
    lifecycle = build_telegram_lifecycle(settings, deps, bot=bot)
    await lifecycle.dispatcher.feed_update(bot, _private_message(1, 21, "one"))
    assert _sent_texts(session) == [deps.strings.dm_welcome]


@pytest.mark.unit
async def test_shutdown_drain_and_cancel() -> None:
    deps, _uow, _catalog = _world()
    session = FakeTelegramSession()
    settings = make_settings(
        environment=Environment.LOCAL,
        telegram_updates_mode=TelegramUpdatesMode.WEBHOOK,
        telegram_bot_token="1:TEST",
        telegram_webhook_base_url="https://example.example",
        telegram_webhook_path_secret="p" * 32,
        telegram_webhook_secret_token="s" * 32,
        telegram_shutdown_grace_seconds=0.05,
    )
    bot = Bot(token="1:TEST", session=session)
    lifecycle = build_telegram_lifecycle(settings, deps, bot=bot)

    async def quick() -> None:
        await asyncio.sleep(0.01)

    async def slow() -> None:
        await asyncio.sleep(10)

    lifecycle.schedule_update(quick())
    lifecycle.schedule_update(slow())
    await lifecycle.shutdown()
    assert session.closed is True
