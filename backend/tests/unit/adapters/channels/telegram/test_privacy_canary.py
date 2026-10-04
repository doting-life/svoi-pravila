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
from aiogram.types import (
    CallbackQuery,
    Chat,
    ChosenInlineResult,
    InlineQuery,
    Message,
    MessageOriginUser,
    Update,
    User,
)
from tests.factories import make_settings
from tests.fakes.consent_catalog import FakeConsentCatalog
from tests.fakes.generation import FakeTextGenerator
from tests.fakes.prepared import FakePreparedResults
from tests.fakes.telegram_deps import TelegramTestDeps, make_telegram_deps
from tests.fakes.telegram_session import FakeTelegramSession
from tests.fakes.uow import InMemoryUnitOfWorkFactory
from tests.fakes.usage_sink import RecordingUsageEventSink

from svoi_pravila.adapters.channels.telegram.deps import TelegramDeps
from svoi_pravila.adapters.channels.telegram.factory import build_telegram_lifecycle
from svoi_pravila.application.inline_result_ref import encode_inline_result_ref
from svoi_pravila.application.ports.generation import (
    DecodeResult,
    GenerationMeta,
    SafetyVerdict,
    TokenUsage,
    Variant,
)
from svoi_pravila.application.ports.prepared_results import PreparedVariant
from svoi_pravila.config import Environment, TelegramUpdatesMode
from svoi_pravila.domain.enums import ConsentKind, Firmness, UsageScenario

_SENTINEL_TEXT = "SENTINEL_TEXT_PRIVACY_0006"
_SENTINEL_FIRST = "SENTINEL_FIRST_PRIVACY_0006"
_SENTINEL_LAST = "SENTINEL_LAST_PRIVACY_0006"
_SENTINEL_USER = "sentinel_user_privacy_0006"
_SENTINEL_ID = 9876543210123
_SENTINEL_ANALYSIS = "SENTINEL_ANALYSIS_PRIVACY_0006"
_SENTINEL_HYP = "SENTINEL_HYPOTHESIS_PRIVACY_0006"
_SENTINEL_VARIANT = "SENTINEL_VARIANT_PRIVACY_0006"
_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _assert_no_markers(blob: str) -> None:
    for marker in (
        _SENTINEL_TEXT,
        _SENTINEL_FIRST,
        _SENTINEL_LAST,
        _SENTINEL_USER,
        str(_SENTINEL_ID),
        _SENTINEL_ANALYSIS,
        _SENTINEL_HYP,
        _SENTINEL_VARIANT,
    ):
        assert marker not in blob


async def _await_inline(deps: TelegramDeps) -> None:
    pending = [task for task in deps.inline_queries.tasks if not task.done()]
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


@pytest.mark.unit
async def test_privacy_canary_no_sentinel_in_logs(
    capture_log_events: Callable[[], list[dict[str, Any]]],
) -> None:
    for name in ("aiogram", "aiogram.event", "aiogram.dispatcher", "aiogram.middlewares"):
        logging.getLogger(name).setLevel(logging.DEBUG)

    uow = InMemoryUnitOfWorkFactory()
    catalog = FakeConsentCatalog()
    sink = RecordingUsageEventSink()
    generator = FakeTextGenerator(
        stream_chunks=(_SENTINEL_ANALYSIS,),
        decode_result=DecodeResult(
            hypotheses=(_SENTINEL_HYP,),
            underlying_request=_SENTINEL_TEXT,
            variants=(Variant(text=_SENTINEL_VARIANT, firmness=Firmness.GENTLE),),
            applied_rule_indexes=(),
            safety=SafetyVerdict.OK,
            meta=GenerationMeta(
                model="fake",
                prompt_version="decode@v1",
                latency_ms=1,
                attempts=1,
                usage=TokenUsage(),
            ),
        ),
    )
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
                text="/start",
            ),
        ),
    )
    await lifecycle.dispatcher.feed_update(
        bot,
        Update(
            update_id=56,
            callback_query=CallbackQuery(
                id="age",
                from_user=origin,
                chat_instance="x",
                data="age:y",
                message=Message(
                    message_id=1,
                    date=_NOW,
                    chat=Chat(id=_SENTINEL_ID, type="private"),
                    from_user=origin,
                    text="age",
                ),
            ),
        ),
    )
    pd = catalog.current_requirement().for_kind(ConsentKind.PERSONAL_DATA).version
    sc = catalog.current_requirement().for_kind(ConsentKind.SPECIAL_CATEGORY).version
    for update_id, data in (
        (57, f"cg:personal_data:{pd}:y"),
        (58, f"cg:special_category:{sc}:y"),
    ):
        await lifecycle.dispatcher.feed_update(
            bot,
            Update(
                update_id=update_id,
                callback_query=CallbackQuery(
                    id=str(update_id),
                    from_user=origin,
                    chat_instance="x",
                    data=data,
                    message=Message(
                        message_id=1,
                        date=_NOW,
                        chat=Chat(id=_SENTINEL_ID, type="private"),
                        from_user=origin,
                        text="c",
                    ),
                ),
            ),
        )
    await lifecycle.dispatcher.feed_update(
        bot,
        Update(
            update_id=59,
            message=Message(
                message_id=2,
                date=_NOW,
                chat=Chat(id=_SENTINEL_ID, type="private"),
                from_user=origin,
                text=_SENTINEL_TEXT,
                forward_origin=MessageOriginUser(date=_NOW, sender_user=origin),
            ),
        ),
    )

    events = capture_log_events()
    blob = json.dumps(events) + "\n".join(str(event) for event in events)
    _assert_no_markers(blob)
    assert len(sink.events) == 1
    recorded = sink.events[0]
    assert recorded.user_pseudonym != str(_SENTINEL_ID)
    assert recorded.scenario.value == "decode"
    assert recorded.surface.value == "dm"
    assert _SENTINEL_TEXT not in (recorded.model or "")
    assert _SENTINEL_TEXT not in (recorded.prompt_version or "")
    assert generator.decode_stream_calls[0].incoming == _SENTINEL_TEXT


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
    await lifecycle.dispatcher.feed_update(
        bot,
        Update(
            update_id=70,
            message=Message(
                message_id=1,
                date=_NOW,
                chat=Chat(id=_SENTINEL_ID, type="private"),
                from_user=origin,
                text="/start",
            ),
        ),
    )
    await lifecycle.dispatcher.feed_update(
        bot,
        Update(
            update_id=71,
            callback_query=CallbackQuery(
                id="age",
                from_user=origin,
                chat_instance="x",
                data="age:y",
                message=Message(
                    message_id=1,
                    date=_NOW,
                    chat=Chat(id=_SENTINEL_ID, type="private"),
                    from_user=origin,
                    text="age",
                ),
            ),
        ),
    )
    pd = catalog.current_requirement().for_kind(ConsentKind.PERSONAL_DATA).version
    sc = catalog.current_requirement().for_kind(ConsentKind.SPECIAL_CATEGORY).version
    for update_id, data in (
        (72, f"cg:personal_data:{pd}:y"),
        (73, f"cg:special_category:{sc}:y"),
    ):
        await lifecycle.dispatcher.feed_update(
            bot,
            Update(
                update_id=update_id,
                callback_query=CallbackQuery(
                    id=str(update_id),
                    from_user=origin,
                    chat_instance="x",
                    data=data,
                    message=Message(
                        message_id=1,
                        date=_NOW,
                        chat=Chat(id=_SENTINEL_ID, type="private"),
                        from_user=origin,
                        text="c",
                    ),
                ),
            ),
        )
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
        assert _SENTINEL_TEXT not in (event.model or "")
        assert _SENTINEL_TEXT not in (event.prompt_version or "")
