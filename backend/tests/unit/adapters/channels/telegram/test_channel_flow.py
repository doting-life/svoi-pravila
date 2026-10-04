"""Telegram channel onboarding, middleware, and lifecycle tests."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
from aiogram import Bot
from aiogram.methods import (
    DeleteWebhook,
    EditMessageReplyMarkup,
    SendMessage,
    SetMyCommands,
    SetWebhook,
)
from aiogram.types import CallbackQuery, Chat, Message, PhotoSize, Update, User
from tests.factories import make_settings
from tests.fakes.clock import FakeClock
from tests.fakes.consent_catalog import FakeConsentCatalog
from tests.fakes.ids import FakeIdGenerator
from tests.fakes.rate_limit import FakePseudonymizer, FakeRateLimiter, FakeUpdateDeduplicator
from tests.fakes.telegram_session import FakeTelegramSession
from tests.fakes.uow import InMemoryUnitOfWorkFactory

from svoi_pravila.adapters.channels.telegram.deps import TelegramDeps
from svoi_pravila.adapters.channels.telegram.factory import build_telegram_lifecycle
from svoi_pravila.adapters.channels.telegram.localization import load_ru_strings
from svoi_pravila.application.use_cases.accept_age_confirmation import AcceptAgeConfirmation
from svoi_pravila.application.use_cases.get_consent_document import GetConsentDocument
from svoi_pravila.application.use_cases.get_onboarding_step import GetOnboardingStep
from svoi_pravila.application.use_cases.get_user_by_telegram_id import GetUserByTelegramId
from svoi_pravila.application.use_cases.grant_consent import GrantConsent
from svoi_pravila.config import Environment, TelegramUpdatesMode
from svoi_pravila.domain.enums import ConsentKind
from svoi_pravila.domain.ids import TelegramUserId

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _world(
    *, rate_limit: int = 30
) -> tuple[TelegramDeps, InMemoryUnitOfWorkFactory, FakeConsentCatalog]:
    uow = InMemoryUnitOfWorkFactory()
    catalog = FakeConsentCatalog()
    clock = FakeClock()
    ids = FakeIdGenerator()
    deps = TelegramDeps(
        strings=load_ru_strings(),
        get_onboarding_step=GetOnboardingStep(uow, catalog),
        get_user_by_telegram_id=GetUserByTelegramId(uow),
        accept_age=AcceptAgeConfirmation(uow, ids, clock),
        grant_consent=GrantConsent(uow, catalog, ids, clock),
        get_consent_document=GetConsentDocument(catalog),
        deduplicator=FakeUpdateDeduplicator(),
        rate_limiter=FakeRateLimiter(limit=rate_limit),
        pseudonymizer=FakePseudonymizer(),
    )
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


def _callback(update_id: int, user_id: int, data: str) -> Update:
    return Update(
        update_id=update_id,
        callback_query=CallbackQuery(
            id=str(update_id),
            from_user=User(id=user_id, is_bot=False, first_name="A"),
            chat_instance="x",
            data=data,
            message=Message(
                message_id=1,
                date=_NOW,
                chat=Chat(id=user_id, type="private"),
                from_user=User(id=user_id, is_bot=False, first_name="A"),
                text="prompt",
            ),
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
    assert SetMyCommands in kinds
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
    await lifecycle.dispatcher.feed_update(bot, update)
    assert len(_sent_texts(session)) == first_count


@pytest.mark.unit
async def test_rate_limit_notifies_once() -> None:
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
        await lifecycle.dispatcher.feed_update(bot, _private_message(update_id, 20, "/help"))
    limited = [text for text in _sent_texts(session) if text == deps.strings.rate_limited]
    assert len(limited) == 1


@pytest.mark.unit
async def test_onboarding_accept_and_decline_paths() -> None:
    deps, uow, catalog = _world()
    session = FakeTelegramSession()
    settings = make_settings(
        environment=Environment.LOCAL,
        telegram_updates_mode=TelegramUpdatesMode.POLLING,
        telegram_bot_token="1:TEST",
    )
    bot = Bot(token="1:TEST", session=session)
    lifecycle = build_telegram_lifecycle(settings, deps, bot=bot)

    await lifecycle.dispatcher.feed_update(bot, _private_message(1, 30, "/start"))
    await lifecycle.dispatcher.feed_update(bot, _callback(2, 30, "age:n"))
    async with uow() as unit:
        assert await unit.users.get_by_telegram_id(TelegramUserId(30)) is None

    await lifecycle.dispatcher.feed_update(bot, _private_message(3, 31, "/start"))
    await lifecycle.dispatcher.feed_update(bot, _callback(4, 31, "age:y"))
    pd_version = catalog.current_requirement().for_kind(ConsentKind.PERSONAL_DATA).version
    sc_version = catalog.current_requirement().for_kind(ConsentKind.SPECIAL_CATEGORY).version
    await lifecycle.dispatcher.feed_update(
        bot, _callback(5, 31, f"cg:personal_data:{pd_version}:y")
    )
    await lifecycle.dispatcher.feed_update(
        bot, _callback(6, 31, f"cg:special_category:{sc_version}:y")
    )
    texts = _sent_texts(session)
    assert any(deps.strings.done_via_bot in text for text in texts)

    before = len(session.requests)
    await lifecycle.dispatcher.feed_update(
        bot, _callback(7, 31, f"cg:personal_data:{pd_version}:y")
    )
    assert len(session.requests) >= before

    await lifecycle.dispatcher.feed_update(bot, _callback(8, 31, "cg:personal_data:999:y"))
    await lifecycle.dispatcher.feed_update(bot, _private_message(9, 31, "hello sentinel"))
    assert deps.strings.help_body in _sent_texts(session)


@pytest.mark.unit
async def test_non_text_message_renders_onboarding_step() -> None:
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
    assert any(isinstance(req, SendMessage) for req in session.requests)
    assert deps.strings.age_prompt in _sent_texts(session)

    await lifecycle.dispatcher.feed_update(bot, _callback(101, 77, "age:y"))
    before = len(_sent_texts(session))
    await lifecycle.dispatcher.feed_update(bot, _private_message(102, 77, "still onboarding"))
    assert len(_sent_texts(session)) > before


@pytest.mark.unit
async def test_onboarding_clears_inline_keyboard() -> None:
    deps, _uow, _catalog = _world()
    session = FakeTelegramSession()
    settings = make_settings(
        environment=Environment.LOCAL,
        telegram_updates_mode=TelegramUpdatesMode.POLLING,
        telegram_bot_token="1:TEST",
    )
    bot = Bot(token="1:TEST", session=session)
    lifecycle = build_telegram_lifecycle(settings, deps, bot=bot)
    await lifecycle.dispatcher.feed_update(bot, _private_message(1, 88, "/start"))
    await lifecycle.dispatcher.feed_update(bot, _callback(2, 88, "age:n"))
    assert any(isinstance(req, EditMessageReplyMarkup) for req in session.requests)


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
