"""Unit tests for the closed-DM welcome handler."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from aiogram import Bot
from aiogram.methods import SendMessage
from aiogram.types import Chat, InlineKeyboardMarkup, Message, MessageEntity, User
from tests.fakes.telegram_deps import TelegramTestDeps, make_telegram_deps
from tests.fakes.telegram_session import FakeTelegramSession
from tests.fakes.welcome_throttle import FakeWelcomeThrottle

from svoi_pravila.adapters.channels.telegram.handlers.welcome import (
    build_welcome_router,
    private_welcome,
)
from svoi_pravila.application.ports.welcome_throttle import WELCOME_THROTTLE_PURPOSE

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _private_message(
    *,
    user_id: int = 10,
    text: str | None = "hi",
    caption: str | None = None,
    entities: list[MessageEntity] | None = None,
    chat_type: str = "private",
    include_from_user: bool = True,
) -> Message:
    from_user = User(id=user_id, is_bot=False, first_name="A") if include_from_user else None
    chat_id = user_id if chat_type == "private" else -100
    return Message(
        message_id=1,
        date=_NOW,
        chat=Chat(id=chat_id, type=chat_type),
        from_user=from_user,
        text=text,
        caption=caption,
        entities=entities,
    )


@pytest.mark.unit
async def test_welcome_replies_once_with_web_app() -> None:
    throttle = FakeWelcomeThrottle()
    deps = make_telegram_deps(TelegramTestDeps(welcome=throttle, miniapp_url="https://app.example"))
    session = FakeTelegramSession()
    bot = Bot(token="1:TEST", session=session)
    message = _private_message(
        text="hello",
        caption="ignored caption",
        entities=[MessageEntity(type="bold", offset=0, length=5)],
    )
    await private_welcome(message, deps, bot)
    sends = [req for req in session.requests if isinstance(req, SendMessage)]
    assert len(sends) == 1
    assert sends[0].text == deps.strings.dm_welcome
    markup = sends[0].reply_markup
    assert isinstance(markup, InlineKeyboardMarkup)
    button = markup.inline_keyboard[0][0]
    assert button.text == deps.strings.dm_open_app
    assert button.web_app is not None
    assert button.web_app.url == "https://app.example"
    pseudonym = deps.pseudonymizer.pseudonymize(WELCOME_THROTTLE_PURPOSE, "10")
    assert await throttle.claim(pseudonym) is False


@pytest.mark.unit
async def test_welcome_throttled_second_call_no_reply() -> None:
    throttle = FakeWelcomeThrottle()
    deps = make_telegram_deps(TelegramTestDeps(welcome=throttle))
    session = FakeTelegramSession()
    bot = Bot(token="1:TEST", session=session)
    await private_welcome(_private_message(), deps, bot)
    await private_welcome(_private_message(text="again"), deps, bot)
    assert len([req for req in session.requests if isinstance(req, SendMessage)]) == 1


@pytest.mark.unit
async def test_welcome_skips_without_from_user_or_private_chat() -> None:
    deps = make_telegram_deps()
    session = FakeTelegramSession()
    bot = Bot(token="1:TEST", session=session)
    await private_welcome(_private_message(include_from_user=False), deps, bot)
    await private_welcome(_private_message(chat_type="group"), deps, bot)
    assert session.requests == []


@pytest.mark.unit
async def test_welcome_skips_when_miniapp_url_missing() -> None:
    deps = make_telegram_deps(TelegramTestDeps(miniapp_url=None))
    session = FakeTelegramSession()
    bot = Bot(token="1:TEST", session=session)
    await private_welcome(_private_message(), deps, bot)
    assert session.requests == []


@pytest.mark.unit
def test_build_welcome_router_registers_message_handler() -> None:
    router = build_welcome_router()
    assert router.message.handlers
    assert router.message.handlers[0].callback is private_welcome
