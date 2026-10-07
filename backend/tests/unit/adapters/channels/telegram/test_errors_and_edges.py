"""Error handler and remaining channel edge-case coverage."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import replace
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
    ChatMemberMember,
    ChatMemberUpdated,
    ErrorEvent,
    InlineQuery,
    Message,
    Update,
    User,
)
from tests.factories import make_settings
from tests.fakes.rate_limit import FakeRateLimiter
from tests.fakes.telegram_deps import make_telegram_deps
from tests.fakes.telegram_session import FakeTelegramSession

from svoi_pravila.adapters.channels.telegram.deps import TelegramDeps
from svoi_pravila.adapters.channels.telegram.errors import telegram_error_handler
from svoi_pravila.adapters.channels.telegram.factory import build_telegram_lifecycle
from svoi_pravila.adapters.channels.telegram.lifecycle import (
    TelegramLifecycle,
    TelegramRuntimeConfig,
)
from svoi_pravila.adapters.channels.telegram.localization import load_ru_strings
from svoi_pravila.adapters.channels.telegram.middlewares.dedup import DedupMiddleware
from svoi_pravila.adapters.channels.telegram.middlewares.private_chat import (
    PrivateChatMiddleware,
)
from svoi_pravila.adapters.channels.telegram.middlewares.rate_limit import RateLimitMiddleware
from svoi_pravila.config import Environment, Settings, TelegramUpdatesMode

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _deps() -> TelegramDeps:
    return make_telegram_deps()


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
async def test_error_handler_skips_non_private_message_chat() -> None:
    deps = _deps()
    session = FakeTelegramSession()
    bot = Bot(token="1:TEST", session=session)
    update = Update(
        update_id=2,
        callback_query=CallbackQuery(
            id="1",
            from_user=User(id=5, is_bot=False, first_name="A"),
            chat_instance="x",
            data="ignored",
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
    assert not any(isinstance(req, SendMessage) for req in session.requests)


@pytest.mark.unit
def test_factory_rejects_disabled_and_missing_token() -> None:
    deps = _deps()
    with pytest.raises(RuntimeError):
        build_telegram_lifecycle(
            make_settings(telegram_updates_mode=TelegramUpdatesMode.DISABLED),
            deps,
        )
    settings = Settings.model_construct(
        telegram_updates_mode=TelegramUpdatesMode.POLLING,
        telegram_bot_token=None,
    )
    with pytest.raises(RuntimeError):
        build_telegram_lifecycle(settings, deps)


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
async def test_error_handler_send_failure_and_no_chat(
    capture_log_events: Callable[[], list[dict[str, Any]]],
) -> None:
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
    events = capture_log_events()
    assert any(event.get("event") == "telegram_error_reply_failed" for event in events)

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
        config=TelegramRuntimeConfig(
            mode=TelegramUpdatesMode.WEBHOOK,
            strings=deps.strings,
            webhook_url=None,
            webhook_secret_token=None,
            shutdown_grace_seconds=0.01,
            inline_queries=deps.inline_queries,
            bot_username=deps.bot_username,
            miniapp_url="https://miniapp.test",
        ),
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

    inline = Update(
        update_id=99,
        inline_query=InlineQuery(
            id="iq",
            from_user=User(id=1, is_bot=False, first_name="A"),
            query="q",
            offset="",
        ),
    )
    assert await PrivateChatMiddleware()(handler, inline, data) == "ok"
    assert await RateLimitMiddleware()(handler, inline, data) == "ok"

    private_message = Update(
        update_id=100,
        message=Message(
            message_id=1,
            date=_NOW,
            chat=Chat(id=1, type="private"),
            from_user=User(id=1, is_bot=False, first_name="A"),
            text="hi",
        ),
    )
    assert await RateLimitMiddleware()(handler, private_message, data) == "ok"

    membership = Update(
        update_id=102,
        my_chat_member=ChatMemberUpdated(
            chat=Chat(id=-100, type="group", title="g"),
            from_user=User(id=1, is_bot=False, first_name="A"),
            date=_NOW,
            old_chat_member=ChatMemberMember(
                user=User(id=1, is_bot=True, first_name="bot"),
            ),
            new_chat_member=ChatMemberMember(
                user=User(id=1, is_bot=True, first_name="bot"),
            ),
        ),
    )
    assert await PrivateChatMiddleware()(handler, membership, data) == "ok"
    assert await RateLimitMiddleware()(handler, membership, data) == "ok"

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
    limited_deps = replace(deps, rate_limiter=FakeRateLimiter(limit=0))
    data["tg_deps"] = limited_deps
    assert await RateLimitMiddleware()(handler, callback_no_message, data) is None
    assert await RateLimitMiddleware()(handler, inline, data) is None
