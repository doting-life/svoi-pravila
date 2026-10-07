"""Privacy canaries: sentinel PII must not appear in logs."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import pytest
from aiogram import Bot
from aiogram.methods import SendMessage
from aiogram.types import (
    Chat,
    ChosenInlineResult,
    InlineQuery,
    Message,
    MessageEntity,
    Update,
    User,
)
from tests.factories import make_settings
from tests.fakes.clock import FakeClock
from tests.fakes.consent_catalog import FakeConsentCatalog
from tests.fakes.generation import FakeTextGenerator
from tests.fakes.ids import FakeIdGenerator
from tests.fakes.prepared import FakePreparedResults
from tests.fakes.telegram_deps import TelegramTestDeps, make_telegram_deps
from tests.fakes.telegram_session import FakeTelegramSession
from tests.fakes.uow import InMemoryUnitOfWorkFactory
from tests.fakes.usage_sink import RecordingUsageEventSink

from svoi_pravila.adapters.channels.telegram.deps import TelegramDeps
from svoi_pravila.adapters.channels.telegram.factory import build_telegram_lifecycle
from svoi_pravila.application.inline_result_ref import encode_inline_result_ref
from svoi_pravila.application.ports.prepared_results import PreparedVariant
from svoi_pravila.application.use_cases.accept_age_confirmation import (
    AcceptAgeConfirmation,
    AcceptAgeConfirmationCommand,
)
from svoi_pravila.application.use_cases.grant_consent import GrantConsent, GrantConsentCommand
from svoi_pravila.config import Environment, TelegramUpdatesMode
from svoi_pravila.domain.enums import ConsentKind, Firmness, UsageScenario
from svoi_pravila.domain.ids import TelegramUserId

_SENTINEL_TEXT = "SENTINEL_TEXT_PRIVACY_0006"
_SENTINEL_FIRST = "SENTINEL_FIRST_PRIVACY_0006"
_SENTINEL_LAST = "SENTINEL_LAST_PRIVACY_0006"
_SENTINEL_USER = "sentinel_user_privacy_0006"
_SENTINEL_ID = 9876543210123
_SENTINEL_VARIANT = "SENTINEL_VARIANT_PRIVACY_0006"
_SENTINEL_CRISIS = "SENTINEL_CRISIS_PRIVACY_0008"
_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _assert_no_markers(blob: str) -> None:
    for marker in (
        _SENTINEL_TEXT,
        _SENTINEL_FIRST,
        _SENTINEL_LAST,
        _SENTINEL_USER,
        str(_SENTINEL_ID),
        _SENTINEL_VARIANT,
        _SENTINEL_CRISIS,
    ):
        assert marker not in blob


async def _await_inline(deps: TelegramDeps) -> None:
    pending = [task for task in deps.inline_queries.tasks if not task.done()]
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


async def _grant(
    uow: InMemoryUnitOfWorkFactory,
    catalog: FakeConsentCatalog,
    telegram_id: int,
) -> None:
    ids = FakeIdGenerator()
    clock = FakeClock()
    accepted = await AcceptAgeConfirmation(uow, ids, clock).execute(
        AcceptAgeConfirmationCommand(TelegramUserId(telegram_id))
    )
    for kind in ConsentKind:
        version = catalog.current_requirement().for_kind(kind).version
        await GrantConsent(uow, catalog, ids, clock).execute(
            GrantConsentCommand(accepted.user.id, kind, version)
        )


@pytest.mark.unit
async def test_welcome_privacy_canary_no_sentinel_in_logs(
    capture_log_events: Callable[[], list[dict[str, Any]]],
) -> None:
    for name in ("aiogram", "aiogram.event", "aiogram.dispatcher", "aiogram.middlewares"):
        logging.getLogger(name).setLevel(logging.DEBUG)

    deps = make_telegram_deps()
    session = FakeTelegramSession()
    settings = make_settings(
        environment=Environment.LOCAL,
        telegram_updates_mode=TelegramUpdatesMode.POLLING,
        telegram_bot_token="1:TEST",
    )
    bot = Bot(token="1:TEST", session=session)
    lifecycle = build_telegram_lifecycle(settings, deps, bot=bot)
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
            update_id=55,
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
    events = capture_log_events()
    blob = json.dumps(events) + "\n".join(str(event) for event in events)
    _assert_no_markers(blob)


@pytest.mark.unit
async def test_privacy_canary_crisis_screen_hit_inline(
    capture_log_events: Callable[[], list[dict[str, Any]]],
) -> None:
    uow = InMemoryUnitOfWorkFactory()
    catalog = FakeConsentCatalog()
    sink = RecordingUsageEventSink()
    generator = FakeTextGenerator()
    deps = make_telegram_deps(
        TelegramTestDeps(uow=uow, catalog=catalog, generator=generator, sink=sink)
    )
    session = FakeTelegramSession()
    settings = make_settings(
        environment=Environment.LOCAL,
        telegram_updates_mode=TelegramUpdatesMode.POLLING,
        telegram_bot_token="1:TEST",
    )
    bot = Bot(token="1:TEST", session=session)
    lifecycle = build_telegram_lifecycle(settings, deps, bot=bot)
    await _grant(uow, catalog, 41)
    crisis_text = f"{_SENTINEL_CRISIS} не хочу жить"
    await lifecycle.dispatcher.feed_update(
        bot,
        Update(
            update_id=5,
            inline_query=InlineQuery(
                id="crisis",
                from_user=User(id=41, is_bot=False, first_name="A"),
                query=crisis_text,
                offset="",
            ),
        ),
    )
    await _await_inline(deps)
    blob = json.dumps(capture_log_events())
    assert _SENTINEL_CRISIS not in blob
    assert generator.soften_calls == []
    assert sink.events[0].outcome.value == "screened"
    assert sink.events[0].surface.value == "inline"


@pytest.mark.unit
async def test_inline_and_choice_privacy_canary(
    capture_log_events: Callable[[], list[dict[str, Any]]],
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    uow = InMemoryUnitOfWorkFactory()
    catalog = FakeConsentCatalog()
    sink = RecordingUsageEventSink()
    generator = FakeTextGenerator()
    prepared = FakePreparedResults()
    deps = make_telegram_deps(
        TelegramTestDeps(
            uow=uow,
            catalog=catalog,
            generator=generator,
            sink=sink,
            prepared=prepared,
        )
    )
    session = FakeTelegramSession()
    settings = make_settings(
        environment=Environment.LOCAL,
        telegram_updates_mode=TelegramUpdatesMode.POLLING,
        telegram_bot_token="1:TEST",
    )
    bot = Bot(token="1:TEST", session=session)
    lifecycle = build_telegram_lifecycle(settings, deps, bot=bot)
    origin = User(
        id=_SENTINEL_ID,
        is_bot=False,
        first_name=_SENTINEL_FIRST,
        last_name=_SENTINEL_LAST,
        username=_SENTINEL_USER,
    )
    await _grant(uow, catalog, _SENTINEL_ID)
    await lifecycle.dispatcher.feed_update(
        bot,
        Update(
            update_id=74,
            inline_query=InlineQuery(
                id="iq",
                from_user=origin,
                query=_SENTINEL_TEXT,
                offset="",
            ),
        ),
    )
    await _await_inline(deps)
    result_id = encode_inline_result_ref(UsageScenario.SOFTEN, Firmness.GENTLE)
    await lifecycle.dispatcher.feed_update(
        bot,
        Update(
            update_id=75,
            chosen_inline_result=ChosenInlineResult(
                result_id=result_id,
                from_user=origin,
                query=_SENTINEL_TEXT,
            ),
        ),
    )
    token = await prepared.store(
        deps.pseudonymizer.pseudonymize("prepared", str(_SENTINEL_ID)),
        PreparedVariant(Firmness.GENTLE, _SENTINEL_VARIANT),
    )
    await lifecycle.dispatcher.feed_update(
        bot,
        Update(
            update_id=76,
            inline_query=InlineQuery(
                id="prep",
                from_user=origin,
                query=token,
                offset="",
            ),
        ),
    )
    events = capture_log_events()
    blob = json.dumps(events) + "\n".join(str(event) for event in events)
    _assert_no_markers(blob)
    for event in sink.events:
        assert event.user_pseudonym != str(_SENTINEL_ID)
        assert event.surface.value == "inline"
        assert _SENTINEL_TEXT not in (event.model or "")
        assert _SENTINEL_TEXT not in (event.prompt_version or "")
