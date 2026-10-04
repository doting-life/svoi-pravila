"""Integration privacy canaries for Telegram onboarding persistence and Valkey."""

from __future__ import annotations

from datetime import UTC, datetime
from urllib.parse import urlsplit, urlunsplit

import pytest
from aiogram import Bot
from aiogram.types import CallbackQuery, Chat, Message, Update, User
from sqlalchemy import MetaData, String, Text, select, text
from sqlalchemy.dialects.postgresql import BYTEA
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from svoi_pravila.adapters.cache.client import close_client, create_client
from svoi_pravila.adapters.cache.concurrency import ValkeyConcurrencyGuard
from svoi_pravila.adapters.cache.deduplicator import ValkeyUpdateDeduplicator
from svoi_pravila.adapters.cache.rate_limiter import ValkeyRateLimiter
from svoi_pravila.adapters.channels.telegram.deps import TelegramDeps
from svoi_pravila.adapters.channels.telegram.factory import build_telegram_lifecycle
from svoi_pravila.adapters.channels.telegram.localization import load_ru_strings
from svoi_pravila.adapters.consents import PackageConsentCatalog
from svoi_pravila.adapters.persistence.uow import SqlAlchemyUnitOfWorkFactory
from svoi_pravila.adapters.persistence.usage_sink import UnitOfWorkUsageEventSink
from svoi_pravila.adapters.system.clock import SystemClock
from svoi_pravila.adapters.system.ids import Uuid7IdGenerator
from svoi_pravila.adapters.system.monotonic import SystemMonotonicClock
from svoi_pravila.application.ports.generation import (
    DecodeResult,
    Firmness,
    GenerationMeta,
    SafetyVerdict,
    TokenUsage,
    Variant,
)
from svoi_pravila.application.use_cases.accept_age_confirmation import AcceptAgeConfirmation
from svoi_pravila.application.use_cases.decode_incoming import DecodeIncoming, DecodeIncomingPorts
from svoi_pravila.application.use_cases.get_consent_document import GetConsentDocument
from svoi_pravila.application.use_cases.get_onboarding_step import GetOnboardingStep
from svoi_pravila.application.use_cases.get_user_by_telegram_id import GetUserByTelegramId
from svoi_pravila.application.use_cases.grant_consent import GrantConsent
from svoi_pravila.config import Environment, Settings, TelegramUpdatesMode
from svoi_pravila.crypto import HmacPseudonymizer
from svoi_pravila.domain.enums import ConsentKind
from tests.factories import make_settings
from tests.fakes.generation import FakeTextGenerator
from tests.fakes.telegram_session import FakeTelegramSession

_SENTINEL_TEXT = "SENTINEL_TEXT_PRIVACY_0006_INT"
_SENTINEL_FIRST = "SENTINEL_FIRST_PRIVACY_0006_INT"
_SENTINEL_LAST = "SENTINEL_LAST_PRIVACY_0006_INT"
_SENTINEL_USER = "sentinel_user_privacy_0006_int"
_SENTINEL_ID = 9876543210999
_SENTINEL_ANALYSIS = "SENTINEL_ANALYSIS_PRIVACY_0006_INT"
_SENTINEL_HYP = "SENTINEL_HYPOTHESIS_PRIVACY_0006_INT"
_SENTINEL_VARIANT = "SENTINEL_VARIANT_PRIVACY_0006_INT"
_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _db15_url(valkey_url: str) -> str:
    parts = urlsplit(valkey_url)
    return urlunsplit((parts.scheme, parts.netloc, "/15", parts.query, parts.fragment))


def _is_text_or_bytea(column_type: object) -> bool:
    return isinstance(column_type, (String, Text, BYTEA))


async def _assert_no_markers_in_text_columns(
    conn: AsyncConnection,
    markers: tuple[str, ...],
) -> None:
    metadata = MetaData()
    await conn.run_sync(metadata.reflect)
    for table in metadata.tables.values():
        columns = [column for column in table.columns if _is_text_or_bytea(column.type)]
        if not columns:
            continue
        rows = (await conn.execute(select(*columns))).all()
        for row in rows:
            for value in row:
                for marker in markers:
                    if marker == str(_SENTINEL_ID) and table.name == "users":
                        # telegram_user_id is intentionally persisted (C1).
                        continue
                    if isinstance(value, (bytes, memoryview)):
                        assert marker.encode("utf-8") not in bytes(value)
                    else:
                        assert marker not in str(value)


@pytest.mark.integration
async def test_privacy_canary_no_sentinel_in_postgres_or_valkey(
    settings: Settings,
    engine: AsyncEngine,
    uow_factory_postgres: SqlAlchemyUnitOfWorkFactory,
) -> None:
    uow_factory = uow_factory_postgres
    valkey = create_client(
        make_settings(
            database_url=settings.database_url.get_secret_value(),
            valkey_url=_db15_url(settings.valkey_url.get_secret_value()),
        )
    )
    await valkey.flushdb()
    catalog = PackageConsentCatalog()
    clock = SystemClock()
    monotonic = SystemMonotonicClock()
    ids = Uuid7IdGenerator()
    pepper = HmacPseudonymizer(settings.pseudonym_pepper_bytes())
    decode = DecodeIncoming(
        DecodeIncomingPorts(
            uow_factory=uow_factory,
            catalog=catalog,
            generator=FakeTextGenerator(
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
            ),
            guard=ValkeyConcurrencyGuard(valkey),
            quota=ValkeyRateLimiter(
                valkey, limit=20, window_seconds=3600, key_prefix="tg:decode:quota"
            ),
            sink=UnitOfWorkUsageEventSink(uow_factory),
            clock=clock,
            monotonic=monotonic,
            ids=ids,
            pseudonymizer=pepper,
            deadline_seconds=45.0,
            decode_model="fake",
        )
    )
    deps = TelegramDeps(
        strings=load_ru_strings(),
        get_onboarding_step=GetOnboardingStep(uow_factory, catalog),
        get_user_by_telegram_id=GetUserByTelegramId(uow_factory),
        accept_age=AcceptAgeConfirmation(uow_factory, ids, clock),
        grant_consent=GrantConsent(uow_factory, catalog, ids, clock),
        get_consent_document=GetConsentDocument(catalog),
        decode_incoming=decode,
        deduplicator=ValkeyUpdateDeduplicator(valkey, ttl_seconds=60),
        rate_limiter=ValkeyRateLimiter(valkey, limit=30, window_seconds=60, key_prefix="tg:rl"),
        pseudonymizer=pepper,
        monotonic=monotonic,
        draft_min_interval_ms=50,
    )
    session = FakeTelegramSession()
    bot = Bot(token="1:TEST", session=session)
    lifecycle = build_telegram_lifecycle(
        make_settings(
            environment=Environment.LOCAL,
            telegram_updates_mode=TelegramUpdatesMode.POLLING,
            telegram_bot_token="1:TEST",
            database_url=settings.database_url.get_secret_value(),
            valkey_url=settings.valkey_url.get_secret_value(),
            pseudonym_pepper=settings.pseudonym_pepper.get_secret_value(),
        ),
        deps,
        bot=bot,
    )

    await lifecycle.dispatcher.feed_update(
        bot,
        Update(
            update_id=100,
            message=Message(
                message_id=1,
                date=_NOW,
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
        ),
    )
    await lifecycle.dispatcher.feed_update(
        bot,
        Update(
            update_id=101,
            callback_query=CallbackQuery(
                id="1",
                from_user=User(
                    id=_SENTINEL_ID,
                    is_bot=False,
                    first_name=_SENTINEL_FIRST,
                    last_name=_SENTINEL_LAST,
                    username=_SENTINEL_USER,
                ),
                chat_instance="x",
                data="age:y",
                message=Message(
                    message_id=1,
                    date=_NOW,
                    chat=Chat(id=_SENTINEL_ID, type="private"),
                    from_user=User(id=_SENTINEL_ID, is_bot=False, first_name="A"),
                    text="age",
                ),
            ),
        ),
    )
    pd = catalog.current_document(ConsentKind.PERSONAL_DATA)
    sc = catalog.current_document(ConsentKind.SPECIAL_CATEGORY)
    for update_id, data in (
        (102, f"cg:personal_data:{pd.version}:y"),
        (103, f"cg:special_category:{sc.version}:y"),
    ):
        await lifecycle.dispatcher.feed_update(
            bot,
            Update(
                update_id=update_id,
                callback_query=CallbackQuery(
                    id=str(update_id),
                    from_user=User(id=_SENTINEL_ID, is_bot=False, first_name="A"),
                    chat_instance="x",
                    data=data,
                    message=Message(
                        message_id=1,
                        date=_NOW,
                        chat=Chat(id=_SENTINEL_ID, type="private"),
                        from_user=User(id=_SENTINEL_ID, is_bot=False, first_name="A"),
                        text="c",
                    ),
                ),
            ),
        )

    await lifecycle.dispatcher.feed_update(
        bot,
        Update(
            update_id=104,
            message=Message(
                message_id=2,
                date=_NOW,
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
        ),
    )

    markers = (
        _SENTINEL_TEXT,
        _SENTINEL_FIRST,
        _SENTINEL_LAST,
        _SENTINEL_USER,
        str(_SENTINEL_ID),
        _SENTINEL_ANALYSIS,
        _SENTINEL_HYP,
        _SENTINEL_VARIANT,
    )
    async with engine.connect() as conn:
        await _assert_no_markers_in_text_columns(conn, markers)
        count = (await conn.execute(text("SELECT count(*) FROM usage_events"))).scalar_one()
        assert count == 1
        row = (
            await conn.execute(
                text(
                    "SELECT user_pseudonym, scenario, surface, outcome, "
                    "unavailable_kind, safety, model, prompt_version, "
                    "latency_ms, ttfc_ms, attempts, input_tokens, "
                    "output_tokens, billable_tokens FROM usage_events"
                )
            )
        ).one()
        assert row.scenario == "decode"
        assert row.surface == "dm"
        assert row.outcome == "ok"
        assert len(row.user_pseudonym) == 64
        assert row.user_pseudonym != str(_SENTINEL_ID)
        for marker in markers:
            assert marker not in row.user_pseudonym
            assert marker not in row.model
            assert marker not in row.prompt_version

    keys = [key async for key in valkey.scan_iter(match="*")]
    for key in keys:
        assert str(_SENTINEL_ID) not in key
        for marker in (_SENTINEL_TEXT, _SENTINEL_FIRST, _SENTINEL_LAST, _SENTINEL_USER):
            assert marker not in key
        value = await valkey.get(key)
        rendered = "" if value is None else str(value)
        for marker in markers:
            assert marker not in rendered

    await valkey.flushdb()
    await close_client(valkey)
