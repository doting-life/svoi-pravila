"""Error handler and remaining channel edge-case coverage."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.methods import SendMessage
from aiogram.types import (
    CallbackQuery,
    Chat,
    ErrorEvent,
    InlineQuery,
    Message,
    MessageEntity,
    Update,
    User,
)
from tests.factories import make_settings
from tests.fakes.clock import FakeClock
from tests.fakes.consent_catalog import FakeConsentCatalog
from tests.fakes.ids import FakeIdGenerator
from tests.fakes.rate_limit import FakePseudonymizer, FakeRateLimiter, FakeUpdateDeduplicator
from tests.fakes.telegram_session import FakeTelegramSession
from tests.fakes.uow import InMemoryUnitOfWorkFactory

from svoi_pravila.adapters.channels.telegram.deps import TelegramDeps
from svoi_pravila.adapters.channels.telegram.errors import telegram_error_handler
from svoi_pravila.adapters.channels.telegram.factory import build_telegram_lifecycle
from svoi_pravila.adapters.channels.telegram.handlers import onboarding as onboarding_handlers
from svoi_pravila.adapters.channels.telegram.keyboards import consent_keyboard
from svoi_pravila.adapters.channels.telegram.lifecycle import TelegramLifecycle
from svoi_pravila.adapters.channels.telegram.localization import load_ru_strings
from svoi_pravila.adapters.channels.telegram.middlewares.dedup import DedupMiddleware
from svoi_pravila.adapters.channels.telegram.middlewares.private_chat import (
    PrivateChatMiddleware,
)
from svoi_pravila.adapters.channels.telegram.middlewares.rate_limit import RateLimitMiddleware
from svoi_pravila.adapters.channels.telegram.presenters import render_step
from svoi_pravila.application.use_cases.accept_age_confirmation import AcceptAgeConfirmation
from svoi_pravila.application.use_cases.get_consent_document import GetConsentDocument
from svoi_pravila.application.use_cases.get_onboarding_step import (
    GetOnboardingStep,
    OnboardingStep,
    OnboardingStepKind,
)
from svoi_pravila.application.use_cases.get_user_by_telegram_id import GetUserByTelegramId
from svoi_pravila.application.use_cases.grant_consent import GrantConsent
from svoi_pravila.config import Environment, Settings, TelegramUpdatesMode
from svoi_pravila.domain.enums import ConsentKind

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _deps() -> TelegramDeps:
    uow = InMemoryUnitOfWorkFactory()
    catalog = FakeConsentCatalog()
    clock = FakeClock()
    ids = FakeIdGenerator()
    return TelegramDeps(
        strings=load_ru_strings(),
        get_onboarding_step=GetOnboardingStep(uow, catalog),
        get_user_by_telegram_id=GetUserByTelegramId(uow),
        accept_age=AcceptAgeConfirmation(uow, ids, clock),
        grant_consent=GrantConsent(uow, catalog, ids, clock),
        get_consent_document=GetConsentDocument(catalog),
        deduplicator=FakeUpdateDeduplicator(),
        rate_limiter=FakeRateLimiter(),
        pseudonymizer=FakePseudonymizer(),
    )


@pytest.mark.unit
async def test_error_handler_logs_and_replies() -> None:
    deps = _deps()
    session = FakeTelegramSession()
    bot = Bot(token="1:TEST", session=session)
    update = Update(
        update_id=1,
        message=Message(
            message_id=1,
            date=_NOW,
            chat=Chat(id=5, type="private"),
            from_user=User(id=5, is_bot=False, first_name="A"),
            text="x",
        ),
    )
    event = ErrorEvent(
        update=update,
        exception=RuntimeError("boom"),
    )
    assert await telegram_error_handler(event, bot, deps) is True
    assert any(req.__class__.__name__ == "SendMessage" for req in session.requests)


@pytest.mark.unit
async def test_error_handler_callback_chat() -> None:
    deps = _deps()
    session = FakeTelegramSession()
    bot = Bot(token="1:TEST", session=session)
    update = Update(
        update_id=2,
        callback_query=CallbackQuery(
            id="1",
            from_user=User(id=5, is_bot=False, first_name="A"),
            chat_instance="x",
            data="age:y",
            message=Message(
                message_id=1,
                date=_NOW,
                chat=Chat(id=9, type="private"),
                from_user=User(id=5, is_bot=False, first_name="A"),
                text="p",
            ),
        ),
    )
    event = ErrorEvent(update=update, exception=ValueError("x"))
    assert await telegram_error_handler(event, bot, deps) is True


@pytest.mark.unit
def test_factory_rejects_disabled_and_missing_token() -> None:
    deps = _deps()
    with pytest.raises(RuntimeError):
        build_telegram_lifecycle(
            make_settings(telegram_updates_mode=TelegramUpdatesMode.DISABLED),
            deps,
        )
    # Bypass Settings validators to hit the factory's defensive token check.
    settings = Settings.model_construct(
        telegram_updates_mode=TelegramUpdatesMode.POLLING,
        telegram_bot_token=None,
    )
    with pytest.raises(RuntimeError):
        build_telegram_lifecycle(settings, deps)


@pytest.mark.unit
def test_consent_keyboard_rejects_oversized_callback() -> None:
    strings = load_ru_strings()
    with pytest.raises(ValueError, match="64 bytes"):
        consent_keyboard(
            strings,
            kind=ConsentKind.PERSONAL_DATA,
            version="v" * 80,
        )


@pytest.mark.unit
def test_render_step_requires_document_for_consent() -> None:
    strings = load_ru_strings()
    with pytest.raises(ValueError, match="consent document"):
        render_step(
            strings,
            OnboardingStep(
                kind=OnboardingStepKind.CONSENT,
                consent_kind=ConsentKind.PERSONAL_DATA,
                consent_version="1",
            ),
            None,
        )


@pytest.mark.unit
def test_localization_rejects_non_object(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    broken = tmp_path / "ru.json"
    broken.write_text(json.dumps(["not", "object"]), encoding="utf-8")

    class _Files:
        def joinpath(self, name: str) -> Path:
            return broken

    monkeypatch.setattr(
        "svoi_pravila.adapters.channels.telegram.localization.resources.files",
        lambda _pkg: _Files(),
    )
    with pytest.raises(TypeError):
        load_ru_strings()


@pytest.mark.unit
async def test_consent_decline_and_help_commands() -> None:
    deps = _deps()
    session = FakeTelegramSession()
    settings = make_settings(
        environment=Environment.LOCAL,
        telegram_updates_mode=TelegramUpdatesMode.POLLING,
        telegram_bot_token="1:TEST",
    )
    bot = Bot(token="1:TEST", session=session)
    lifecycle = build_telegram_lifecycle(settings, deps, bot=bot)
    await lifecycle.dispatcher.feed_update(
        bot,
        Update(
            update_id=1,
            message=Message(
                message_id=1,
                date=_NOW,
                chat=Chat(id=40, type="private"),
                from_user=User(id=40, is_bot=False, first_name="A"),
                text="/help",
                entities=[MessageEntity(type="bot_command", offset=0, length=5)],
            ),
        ),
    )
    await lifecycle.dispatcher.feed_update(
        bot,
        Update(
            update_id=2,
            callback_query=CallbackQuery(
                id="2",
                from_user=User(id=40, is_bot=False, first_name="A"),
                chat_instance="x",
                data="cg:personal_data:1:n",
                message=Message(
                    message_id=1,
                    date=_NOW,
                    chat=Chat(id=40, type="private"),
                    from_user=User(id=40, is_bot=False, first_name="A"),
                    text="c",
                ),
            ),
        ),
    )
    texts = [str(getattr(req, "text", "")) for req in session.requests]
    assert deps.strings.help_body in texts
    assert deps.strings.consent_declined in texts


@pytest.mark.unit
async def test_error_handler_send_failure_and_no_chat() -> None:
    deps = _deps()
    session = FakeTelegramSession()
    bot = Bot(token="1:TEST", session=session)
    update = Update(
        update_id=3,
        message=Message(
            message_id=1,
            date=_NOW,
            chat=Chat(id=5, type="private"),
            from_user=User(id=5, is_bot=False, first_name="A"),
            text="x",
        ),
    )
    event = ErrorEvent(update=update, exception=RuntimeError("boom"))
    failing_send = AsyncMock(
        side_effect=TelegramAPIError(method=SendMessage(chat_id=5, text="x"), message="fail")
    )
    object.__setattr__(bot, "send_message", failing_send)
    assert await telegram_error_handler(event, bot, deps) is True

    empty = ErrorEvent(
        update=Update(
            update_id=4,
            inline_query=InlineQuery(
                id="iq",
                from_user=User(id=1, is_bot=False, first_name="A"),
                query="q",
                offset="",
            ),
        ),
        exception=RuntimeError("nochat"),
    )
    assert await telegram_error_handler(empty, bot, deps) is True


@pytest.mark.unit
def test_factory_rejects_incomplete_webhook() -> None:
    deps = _deps()
    settings = Settings.model_construct(
        telegram_updates_mode=TelegramUpdatesMode.WEBHOOK,
        telegram_bot_token=make_settings(
            environment=Environment.LOCAL,
            telegram_updates_mode=TelegramUpdatesMode.POLLING,
            telegram_bot_token="1:TEST",
        ).telegram_bot_token,
        telegram_webhook_base_url=None,
        telegram_webhook_path_secret=None,
        telegram_webhook_secret_token=None,
    )
    with pytest.raises(RuntimeError, match="incomplete"):
        build_telegram_lifecycle(settings, deps)


@pytest.mark.unit
async def test_lifecycle_webhook_requires_url() -> None:
    deps = _deps()
    session = FakeTelegramSession()
    bot = Bot(token="1:TEST", session=session)
    lifecycle = TelegramLifecycle(
        bot=bot,
        dispatcher=build_telegram_lifecycle(
            make_settings(
                environment=Environment.LOCAL,
                telegram_updates_mode=TelegramUpdatesMode.POLLING,
                telegram_bot_token="1:TEST",
            ),
            deps,
            bot=bot,
        ).dispatcher,
        mode=TelegramUpdatesMode.WEBHOOK,
        strings=deps.strings,
        webhook_url=None,
        webhook_secret_token=None,
        shutdown_grace_seconds=0.01,
    )
    with pytest.raises(RuntimeError, match="webhook url"):
        await lifecycle.start()


@pytest.mark.unit
async def test_middlewares_ignore_non_update_events() -> None:
    deps = _deps()
    handler = AsyncMock(return_value="ok")
    data: dict[str, Any] = {"tg_deps": deps, "bot": Bot(token="1:TEST")}
    not_update = Message(
        message_id=1,
        date=_NOW,
        chat=Chat(id=1, type="private"),
        text="x",
    )
    assert await PrivateChatMiddleware()(handler, not_update, data) is None
    assert await DedupMiddleware()(handler, not_update, data) is None
    assert await RateLimitMiddleware()(handler, not_update, data) is None
    handler.assert_not_awaited()


@pytest.mark.unit
async def test_middlewares_edge_updates() -> None:
    deps = _deps()
    handler = AsyncMock(return_value="ok")
    bot = Bot(token="1:TEST", session=FakeTelegramSession())
    data: dict[str, Any] = {"tg_deps": deps, "bot": bot}

    empty = Update(
        update_id=99,
        inline_query=InlineQuery(
            id="iq",
            from_user=User(id=1, is_bot=False, first_name="A"),
            query="q",
            offset="",
        ),
    )
    assert await PrivateChatMiddleware()(handler, empty, data) is None
    assert await RateLimitMiddleware()(handler, empty, data) is None

    no_from = Update(
        update_id=100,
        message=Message(
            message_id=1,
            date=_NOW,
            chat=Chat(id=1, type="private"),
            text="hi",
        ),
    )
    assert await RateLimitMiddleware()(handler, no_from, data) is None

    callback_no_message = Update(
        update_id=101,
        callback_query=CallbackQuery(
            id="1",
            from_user=User(id=7, is_bot=False, first_name="A"),
            chat_instance="x",
            data="age:y",
        ),
    )
    assert await PrivateChatMiddleware()(handler, callback_no_message, data) is None
    limited_deps = TelegramDeps(
        strings=deps.strings,
        get_onboarding_step=deps.get_onboarding_step,
        get_user_by_telegram_id=deps.get_user_by_telegram_id,
        accept_age=deps.accept_age,
        grant_consent=deps.grant_consent,
        get_consent_document=deps.get_consent_document,
        deduplicator=deps.deduplicator,
        rate_limiter=FakeRateLimiter(limit=0),
        pseudonymizer=deps.pseudonymizer,
    )
    data["tg_deps"] = limited_deps
    assert await RateLimitMiddleware()(handler, callback_no_message, data) is None


@pytest.mark.unit
def test_parse_consent_callback_edges() -> None:
    assert onboarding_handlers._parse_consent_callback("bad") is None
    assert onboarding_handlers._parse_consent_callback("cg:nope:1:y") is None
    assert onboarding_handlers._parse_consent_callback("cg:personal_data:1:x") is None
    assert onboarding_handlers._parse_consent_callback("cg:personal_data:1:n") == (
        ConsentKind.PERSONAL_DATA,
        "1",
        False,
    )


@pytest.mark.unit
async def test_onboarding_handler_edges() -> None:
    deps = _deps()
    session = FakeTelegramSession()
    settings = make_settings(
        environment=Environment.LOCAL,
        telegram_updates_mode=TelegramUpdatesMode.POLLING,
        telegram_bot_token="1:TEST",
    )
    bot = Bot(token="1:TEST", session=session)
    lifecycle = build_telegram_lifecycle(settings, deps, bot=bot)

    # /start without from_user is ignored
    await lifecycle.dispatcher.feed_update(
        bot,
        Update(
            update_id=50,
            message=Message(
                message_id=1,
                date=_NOW,
                chat=Chat(id=50, type="private"),
                text="/start",
                entities=[MessageEntity(type="bot_command", offset=0, length=6)],
            ),
        ),
    )
    assert session.requests == []

    # plain text while still on AGE re-renders the age step
    await lifecycle.dispatcher.feed_update(
        bot,
        Update(
            update_id=51,
            message=Message(
                message_id=2,
                date=_NOW,
                chat=Chat(id=51, type="private"),
                from_user=User(id=51, is_bot=False, first_name="A"),
                text="hello",
            ),
        ),
    )
    assert any(isinstance(req, SendMessage) for req in session.requests)

    # unknown callback while onboarding re-renders
    before = len(session.requests)
    await lifecycle.dispatcher.feed_update(
        bot,
        Update(
            update_id=52,
            callback_query=CallbackQuery(
                id="52",
                from_user=User(id=51, is_bot=False, first_name="A"),
                chat_instance="x",
                data="unknown:x",
                message=Message(
                    message_id=1,
                    date=_NOW,
                    chat=Chat(id=51, type="private"),
                    from_user=User(id=51, is_bot=False, first_name="A"),
                    text="p",
                ),
            ),
        ),
    )
    assert len(session.requests) > before

    # consent grant without prior age confirmation re-renders current step
    await lifecycle.dispatcher.feed_update(
        bot,
        Update(
            update_id=53,
            callback_query=CallbackQuery(
                id="53",
                from_user=User(id=52, is_bot=False, first_name="A"),
                chat_instance="x",
                data="cg:personal_data:1:y",
                message=Message(
                    message_id=1,
                    date=_NOW,
                    chat=Chat(id=52, type="private"),
                    from_user=User(id=52, is_bot=False, first_name="A"),
                    text="p",
                ),
            ),
        ),
    )

    # invalid consent callback shape is answered and ignored
    await lifecycle.dispatcher.feed_update(
        bot,
        Update(
            update_id=54,
            callback_query=CallbackQuery(
                id="54",
                from_user=User(id=52, is_bot=False, first_name="A"),
                chat_instance="x",
                data="cg:personal_data:1:maybe",
                message=Message(
                    message_id=1,
                    date=_NOW,
                    chat=Chat(id=52, type="private"),
                    from_user=User(id=52, is_bot=False, first_name="A"),
                    text="p",
                ),
            ),
        ),
    )

    # age decline / consent decline without message chat
    callback_no_msg = CallbackQuery(
        id="55",
        from_user=User(id=55, is_bot=False, first_name="A"),
        chat_instance="x",
        data="age:n",
    )
    assert onboarding_handlers._callback_chat_id(callback_no_msg) is None
    await onboarding_handlers._send_current_step(bot, callback_no_msg, deps, 55)
