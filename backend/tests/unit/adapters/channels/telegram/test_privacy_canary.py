"""Privacy canaries: sentinel PII must not appear in logs."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import pytest
from aiogram import Bot
from aiogram.types import Chat, Message, Update, User
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

_SENTINEL_TEXT = "SENTINEL_TEXT_PRIVACY_0006"
_SENTINEL_FIRST = "SENTINEL_FIRST_PRIVACY_0006"
_SENTINEL_LAST = "SENTINEL_LAST_PRIVACY_0006"
_SENTINEL_USER = "sentinel_user_privacy_0006"
_SENTINEL_ID = 9876543210123


@pytest.mark.unit
async def test_privacy_canary_no_sentinel_in_logs(
    capture_log_events: Callable[[], list[dict[str, Any]]],
) -> None:
    for name in ("aiogram", "aiogram.event", "aiogram.dispatcher", "aiogram.middlewares"):
        logging.getLogger(name).setLevel(logging.DEBUG)

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
        rate_limiter=FakeRateLimiter(),
        pseudonymizer=FakePseudonymizer(),
    )
    session = FakeTelegramSession()
    settings = make_settings(
        environment=Environment.LOCAL,
        telegram_updates_mode=TelegramUpdatesMode.POLLING,
        telegram_bot_token="1:TEST",
    )
    bot = Bot(token="1:TEST", session=session)
    lifecycle = build_telegram_lifecycle(settings, deps, bot=bot)
    update = Update(
        update_id=55,
        message=Message(
            message_id=1,
            date=datetime(2026, 1, 1, tzinfo=UTC),
            chat=Chat(id=_SENTINEL_ID, type="private"),
            from_user=User(
                id=_SENTINEL_ID,
                is_bot=False,
                first_name=_SENTINEL_FIRST,
                last_name=_SENTINEL_LAST,
                username=_SENTINEL_USER,
            ),
            text=_SENTINEL_TEXT,
        ),
    )
    await lifecycle.dispatcher.feed_update(bot, update)

    blob = "\n".join(str(event) for event in capture_log_events())
    for marker in (
        _SENTINEL_TEXT,
        _SENTINEL_FIRST,
        _SENTINEL_LAST,
        _SENTINEL_USER,
        str(_SENTINEL_ID),
    ):
        assert marker not in blob
