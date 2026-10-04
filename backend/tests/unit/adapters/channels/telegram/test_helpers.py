"""Shared Telegram handler helpers."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.methods import EditMessageReplyMarkup, SendMessage
from aiogram.types import CallbackQuery, Chat, Message, Update, User
from tests.factories import make_settings
from tests.fakes.consent_catalog import FakeConsentCatalog
from tests.fakes.rate_limit import FakePseudonymizer
from tests.fakes.telegram_deps import TelegramTestDeps, make_telegram_deps
from tests.fakes.telegram_session import FakeTelegramSession
from tests.fakes.uow import InMemoryUnitOfWorkFactory

from svoi_pravila.adapters.channels.telegram.factory import build_telegram_lifecycle
from svoi_pravila.adapters.channels.telegram.handlers.helpers import (
    actor,
    callback_chat_id,
    clear_callback_keyboard,
    dialog_pseudonym,
    onboarding_payload,
    reply_callback,
    require_done_callback,
    send_current_step,
)
from svoi_pravila.application.ports.dialog_state import DIALOG_PSEUDONYM_PURPOSE
from svoi_pravila.application.use_cases.get_onboarding_step import OnboardingStepKind
from svoi_pravila.config import Environment, Settings, TelegramUpdatesMode
from svoi_pravila.domain.enums import ConsentKind

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _settings() -> Settings:
    return make_settings(
        environment=Environment.LOCAL,
        telegram_updates_mode=TelegramUpdatesMode.POLLING,
        telegram_bot_token="1:TEST",
    )


def _message(user_id: int, text: str) -> Message:
    return Message(
        message_id=1,
        date=_NOW,
        chat=Chat(id=user_id, type="private"),
        from_user=User(id=user_id, is_bot=False, first_name="A"),
        text=text,
    )


def _callback(
    user_id: int, data: str, *, cid: str = "1", with_message: bool = True
) -> CallbackQuery:
    message = None
    if with_message:
        message = Message(
            message_id=1,
            date=_NOW,
            chat=Chat(id=user_id, type="private"),
            from_user=User(id=user_id, is_bot=False, first_name="A"),
            text="p",
        )
    return CallbackQuery(
        id=cid,
        from_user=User(id=user_id, is_bot=False, first_name="A"),
        chat_instance="x",
        data=data,
        message=message,
    )


@pytest.mark.unit
async def test_helpers_callback_chat_and_actor() -> None:
    deps = make_telegram_deps()
    assert callback_chat_id(_callback(1, "age:n", with_message=False)) is None
    assert callback_chat_id(_callback(1, "age:n")) == 1
    assert dialog_pseudonym(deps, 9) == FakePseudonymizer().pseudonymize(
        DIALOG_PSEUDONYM_PURPOSE, "9"
    )
    assert await actor(deps, 1) is None


@pytest.mark.unit
async def test_helpers_onboarding_gates_and_payload() -> None:
    uow = InMemoryUnitOfWorkFactory()
    catalog = FakeConsentCatalog()
    deps = make_telegram_deps(TelegramTestDeps(uow=uow, catalog=catalog))
    session = FakeTelegramSession()
    bot = Bot(token="1:TEST", session=session)
    lifecycle = build_telegram_lifecycle(_settings(), deps, bot=bot)
    assert await require_done_callback(_callback(640, "age:n"), deps, bot) is False
    await lifecycle.dispatcher.feed_update(
        bot, Update(update_id=1, message=_message(640, "/start"))
    )
    await lifecycle.dispatcher.feed_update(
        bot, Update(update_id=2, callback_query=_callback(640, "age:y", cid="2"))
    )
    step, document = await onboarding_payload(deps, 640)
    assert step.kind is OnboardingStepKind.CONSENT
    assert document is not None
    assert document.kind is ConsentKind.PERSONAL_DATA
    await send_current_step(bot, _callback(640, "x"), deps, 640)
    pd = catalog.current_requirement().for_kind(ConsentKind.PERSONAL_DATA).version
    sc = catalog.current_requirement().for_kind(ConsentKind.SPECIAL_CATEGORY).version
    await lifecycle.dispatcher.feed_update(
        bot,
        Update(update_id=3, callback_query=_callback(640, f"cg:personal_data:{pd}:y", cid="3")),
    )
    await lifecycle.dispatcher.feed_update(
        bot,
        Update(update_id=4, callback_query=_callback(640, f"cg:special_category:{sc}:y", cid="4")),
    )
    assert await require_done_callback(_callback(640, "ru:n", cid="5"), deps, bot) is True
    assert await actor(deps, 640) is not None
    await reply_callback(bot, _callback(640, "x"), "hello")
    await reply_callback(bot, _callback(640, "x", with_message=False), "skip")
    assert any(isinstance(req, SendMessage) and req.text == "hello" for req in session.requests)


@pytest.mark.unit
async def test_helpers_clear_keyboard_paths() -> None:
    deps = make_telegram_deps()
    session = FakeTelegramSession()
    bot = Bot(token="1:TEST", session=session)
    await clear_callback_keyboard(bot, _callback(1, "age:n", with_message=False))
    failing = AsyncMock(
        side_effect=TelegramAPIError(
            method=EditMessageReplyMarkup(chat_id=1, message_id=1),
            message="too old",
        )
    )
    object.__setattr__(bot, "edit_message_reply_markup", failing)
    await clear_callback_keyboard(bot, _callback(1, "age:n"))
    await send_current_step(bot, _callback(1, "age:n", with_message=False), deps, 1)
