"""Webhook endpoint behaviour."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator
from typing import Any, cast, override

import pytest
from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.methods import TelegramMethod
from aiogram.methods.base import TelegramType
from aiogram.types import Update
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from tests.factories import make_settings
from tests.fakes.clock import FakeClock
from tests.fakes.consent_catalog import FakeConsentCatalog
from tests.fakes.ids import FakeIdGenerator
from tests.fakes.probes import OkProbe
from tests.fakes.rate_limit import FakePseudonymizer, FakeRateLimiter, FakeUpdateDeduplicator
from tests.fakes.telegram_session import FakeTelegramSession
from tests.fakes.uow import InMemoryUnitOfWorkFactory

from svoi_pravila.adapters.channels.telegram.deps import TelegramDeps
from svoi_pravila.adapters.channels.telegram.factory import build_telegram_lifecycle
from svoi_pravila.adapters.channels.telegram.lifecycle import TelegramLifecycle
from svoi_pravila.adapters.channels.telegram.localization import load_ru_strings
from svoi_pravila.api.app import AppLifecycleHooks, create_app
from svoi_pravila.api.telegram_webhook import (
    TelegramWebhookBindings,
    build_telegram_webhook_router,
)
from svoi_pravila.application.use_cases.accept_age_confirmation import AcceptAgeConfirmation
from svoi_pravila.application.use_cases.check_readiness import CheckReadiness
from svoi_pravila.application.use_cases.get_consent_document import GetConsentDocument
from svoi_pravila.application.use_cases.get_onboarding_step import GetOnboardingStep
from svoi_pravila.application.use_cases.get_user_by_telegram_id import GetUserByTelegramId
from svoi_pravila.application.use_cases.grant_consent import GrantConsent
from svoi_pravila.config import Environment, TelegramUpdatesMode

_PATH = "p" * 32
_SECRET = "s" * 32


class _BlockingSession(BaseSession):
    """Block the first SendMessage until ``gate`` is set."""

    def __init__(self, gate: asyncio.Event) -> None:
        super().__init__()
        self.gate = gate
        self.requests: list[TelegramMethod[Any]] = []

    @override
    async def close(self) -> None:
        return None

    @override
    async def make_request(
        self,
        bot: Bot,
        method: TelegramMethod[TelegramType],
        timeout: int | None = None,
    ) -> TelegramType:
        self.requests.append(method)
        if method.__class__.__name__ == "SendMessage":
            await self.gate.wait()
        ok: bool = True
        return cast(TelegramType, ok)

    @override
    async def stream_content(
        self,
        url: str,
        headers: dict[str, Any] | None = None,
        timeout: int = 30,
        chunk_size: int = 65536,
        raise_for_status: bool = True,
    ) -> AsyncGenerator[bytes]:
        _ = raise_for_status
        yield b""


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


def _lifecycle(session: BaseSession | None = None) -> TelegramLifecycle:
    settings = make_settings(
        environment=Environment.LOCAL,
        telegram_updates_mode=TelegramUpdatesMode.WEBHOOK,
        telegram_bot_token="1:TEST",
        telegram_webhook_base_url="https://example.example",
        telegram_webhook_path_secret=_PATH,
        telegram_webhook_secret_token=_SECRET,
    )
    bot = Bot(token="1:TEST", session=session or FakeTelegramSession())
    return build_telegram_lifecycle(settings, _deps(), bot=bot)


def _app(lifecycle: TelegramLifecycle) -> FastAPI:
    async def _feed(update: Update) -> None:
        await lifecycle.dispatcher.feed_update(lifecycle.bot, update)

    router = build_telegram_webhook_router(
        TelegramWebhookBindings(
            is_accepting=lambda: lifecycle.accepting,
            path_secret=_PATH,
            secret_token=_SECRET,
            parse_bot=lifecycle.bot,
            feed_update=_feed,
            schedule=lifecycle.schedule_update,
        )
    )
    return create_app(
        CheckReadiness(probes=(OkProbe("x"),), timeout_seconds=1.0),
        Environment.TEST,
        AppLifecycleHooks(extra_routers=(router,)),
    )


_PRIVATE_START = {
    "update_id": 42,
    "message": {
        "message_id": 1,
        "date": 1,
        "chat": {"id": 7, "type": "private"},
        "from": {"id": 7, "is_bot": False, "first_name": "A"},
        "text": "/start",
        "entities": [{"offset": 0, "length": 6, "type": "bot_command"}],
    },
}


@pytest.mark.unit
async def test_webhook_auth_matrix() -> None:
    lifecycle = _lifecycle()
    app = _app(lifecycle)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        bad_path = await client.post(
            f"/telegram/webhook/{'x' * 32}",
            content=json.dumps(_PRIVATE_START),
            headers={"X-Telegram-Bot-Api-Secret-Token": _SECRET},
        )
        assert bad_path.status_code == 404
        bad_header = await client.post(
            f"/telegram/webhook/{_PATH}",
            content=json.dumps(_PRIVATE_START),
            headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"},
        )
        assert bad_header.status_code == 404


@pytest.mark.unit
async def test_webhook_size_and_bad_json() -> None:
    lifecycle = _lifecycle()
    app = _app(lifecycle)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        huge = await client.post(
            f"/telegram/webhook/{_PATH}",
            content=b"x" * (256 * 1024 + 1),
            headers={"X-Telegram-Bot-Api-Secret-Token": _SECRET},
        )
        assert huge.status_code == 413
        bad = await client.post(
            f"/telegram/webhook/{_PATH}",
            content=b"{not-json",
            headers={"X-Telegram-Bot-Api-Secret-Token": _SECRET},
        )
        assert bad.status_code == 400
        invalid = await client.post(
            f"/telegram/webhook/{_PATH}",
            content=json.dumps({"update_id": "nope"}),
            headers={"X-Telegram-Bot-Api-Secret-Token": _SECRET},
        )
        assert invalid.status_code == 400


@pytest.mark.unit
async def test_webhook_returns_200_while_handler_blocked() -> None:
    gate = asyncio.Event()
    session = _BlockingSession(gate)
    lifecycle = _lifecycle(session)
    app = _app(lifecycle)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            f"/telegram/webhook/{_PATH}",
            content=json.dumps(_PRIVATE_START),
            headers={"X-Telegram-Bot-Api-Secret-Token": _SECRET},
        )
        assert response.status_code == 200
        assert not gate.is_set()
        gate.set()
        await asyncio.sleep(0.05)


@pytest.mark.unit
async def test_webhook_503_when_not_accepting() -> None:
    lifecycle = _lifecycle()
    lifecycle.stop_accepting()
    app = _app(lifecycle)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            f"/telegram/webhook/{_PATH}",
            content=json.dumps({"update_id": 1}),
            headers={"X-Telegram-Bot-Api-Secret-Token": _SECRET},
        )
        assert response.status_code == 503
