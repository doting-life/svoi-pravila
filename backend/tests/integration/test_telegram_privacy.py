"""Integration privacy canaries for Telegram onboarding persistence and Valkey."""

from __future__ import annotations

import base64
import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from zoneinfo import ZoneInfo

import pytest
from aiogram import Bot
from aiogram.types import CallbackQuery, Chat, Message, Update, User
from redis.asyncio import Redis
from sqlalchemy import MetaData, String, Text, select, text
from sqlalchemy.dialects.postgresql import BYTEA
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from svoi_pravila.adapters.cache.client import close_client, create_client
from svoi_pravila.adapters.cache.concurrency import ValkeyConcurrencyGuard
from svoi_pravila.adapters.cache.confirmation_tokens import ValkeyConfirmationTokens
from svoi_pravila.adapters.cache.deduplicator import ValkeyUpdateDeduplicator
from svoi_pravila.adapters.cache.dialog_state import ValkeyDialogState
from svoi_pravila.adapters.cache.prepared_results import ValkeyPreparedResults
from svoi_pravila.adapters.cache.rate_limiter import ValkeyRateLimiter
from svoi_pravila.adapters.cache.rule_sources import ValkeyRuleSources
from svoi_pravila.adapters.channels.telegram.deps import TelegramDeps
from svoi_pravila.adapters.channels.telegram.factory import build_telegram_lifecycle
from svoi_pravila.adapters.channels.telegram.inline_scheduler import InlineQueryCoordinator
from svoi_pravila.adapters.channels.telegram.lifecycle import TelegramLifecycle
from svoi_pravila.adapters.channels.telegram.localization import (
    help_say_intent_prefixes,
    load_ru_strings,
)
from svoi_pravila.adapters.channels.telegram.sleeper import AsyncioSleeper
from svoi_pravila.adapters.consents import PackageConsentCatalog
from svoi_pravila.adapters.persistence.uow import SqlAlchemyUnitOfWorkFactory
from svoi_pravila.adapters.persistence.usage_sink import UnitOfWorkUsageEventSink
from svoi_pravila.adapters.system.clock import SystemClock
from svoi_pravila.adapters.system.ids import Uuid7IdGenerator
from svoi_pravila.adapters.system.monotonic import SystemMonotonicClock
from svoi_pravila.adapters.system.tone_suggestion_catalog import StaticToneSuggestionCatalog
from svoi_pravila.application.crisis_screen import CrisisScreen
from svoi_pravila.application.ports.generation import (
    DecodeResult,
    GenerationMeta,
    SafetyVerdict,
    TokenUsage,
    Variant,
)
from svoi_pravila.application.use_cases.accept_age_confirmation import AcceptAgeConfirmation
from svoi_pravila.application.use_cases.accept_suggestion import AcceptSuggestion
from svoi_pravila.application.use_cases.archive_rule import ArchiveRule
from svoi_pravila.application.use_cases.create_contact import CreateContact
from svoi_pravila.application.use_cases.decode_incoming import DecodeIncoming, DecodeIncomingPorts
from svoi_pravila.application.use_cases.delete_my_account import DeleteMyAccount
from svoi_pravila.application.use_cases.dismiss_suggestion import DismissSuggestion
from svoi_pravila.application.use_cases.export_my_data import ExportMyData
from svoi_pravila.application.use_cases.get_consent_document import GetConsentDocument
from svoi_pravila.application.use_cases.get_onboarding_step import GetOnboardingStep
from svoi_pravila.application.use_cases.get_user_by_telegram_id import GetUserByTelegramId
from svoi_pravila.application.use_cases.grant_consent import GrantConsent
from svoi_pravila.application.use_cases.inline_compose import InlineCompose, InlineComposePorts
from svoi_pravila.application.use_cases.list_contacts import ListContacts
from svoi_pravila.application.use_cases.list_rules import ListRules
from svoi_pravila.application.use_cases.list_suggestions import ListSuggestions
from svoi_pravila.application.use_cases.propose_rule import ProposeRule
from svoi_pravila.application.use_cases.record_inline_choice import (
    RecordInlineChoice,
    RecordInlineChoicePorts,
)
from svoi_pravila.application.use_cases.rename_contact import RenameContact
from svoi_pravila.application.use_cases.revoke_all_consents import RevokeAllConsents
from svoi_pravila.application.use_cases.set_active_contact import SetActiveContact
from svoi_pravila.application.use_cases.suggest_rule_from_decode import (
    SuggestRuleFromDecode,
    SuggestRuleFromDecodePorts,
)
from svoi_pravila.config import Environment, Settings, TelegramUpdatesMode
from svoi_pravila.crypto import HmacPseudonymizer
from svoi_pravila.domain.enums import ConsentKind, Firmness
from svoi_pravila.domain.ids import TelegramUserId
from svoi_pravila.domain.rules import ContactScope
from tests.factories import make_settings
from tests.fakes.generation import FakeTextGenerator
from tests.fakes.inline_reuse import make_inline_reuse
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


def _uid_message(uid: int, update_id: int, text: str) -> Update:
    return Update(
        update_id=update_id,
        message=Message(
            message_id=update_id,
            date=_NOW,
            chat=Chat(id=uid, type="private"),
            from_user=User(id=uid, is_bot=False, first_name="A"),
            text=text,
        ),
    )


def _uid_callback(uid: int, update_id: int, data: str) -> Update:
    return Update(
        update_id=update_id,
        callback_query=CallbackQuery(
            id=str(update_id),
            from_user=User(id=uid, is_bot=False, first_name="A"),
            chat_instance="x",
            data=data,
            message=Message(
                message_id=1,
                date=_NOW,
                chat=Chat(id=uid, type="private"),
                from_user=User(id=uid, is_bot=False, first_name="A"),
                text="p",
            ),
        ),
    )


async def _assert_valkey_without_sentinel(valkey: Redis, sentinel: str) -> None:
    keys = [key async for key in valkey.scan_iter(match="*")]
    for key in keys:
        assert sentinel not in str(key)
        value = await valkey.get(key)
        rendered = "" if value is None else str(value)
        assert sentinel not in rendered


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
    strings = load_ru_strings()
    sink = UnitOfWorkUsageEventSink(uow_factory)
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
            sink=sink,
            clock=clock,
            monotonic=monotonic,
            ids=ids,
            pseudonymizer=pepper,
            crisis_screen=CrisisScreen.load_ru_v2(),
            deadline_seconds=45.0,
        )
    )
    reuse = make_inline_reuse(monotonic, ttl_seconds=30.0)
    compose = InlineCompose(
        InlineComposePorts(
            uow_factory=uow_factory,
            catalog=catalog,
            generator=FakeTextGenerator(),
            quota=ValkeyRateLimiter(
                valkey, limit=30, window_seconds=3600, key_prefix="tg:inline:quota"
            ),
            sink=sink,
            clock=clock,
            monotonic=monotonic,
            ids=ids,
            pseudonymizer=pepper,
            crisis_screen=CrisisScreen.load_ru_v2(),
            reuse=reuse,
            min_chars=8,
            deadline_seconds=8.0,
            intent_prefixes=help_say_intent_prefixes(strings),
        )
    )
    rule_sources = ValkeyRuleSources(valkey, ttl_seconds=600)
    suggest_rule_from_decode = SuggestRuleFromDecode(
        SuggestRuleFromDecodePorts(
            uow_factory=uow_factory,
            catalog=catalog,
            rule_sources=rule_sources,
            generator=FakeTextGenerator(),
            quota=ValkeyRateLimiter(
                valkey, limit=10, window_seconds=3600, key_prefix="tg:suggest:quota"
            ),
            sink=sink,
            clock=clock,
            monotonic=monotonic,
            ids=ids,
            pseudonymizer=pepper,
            crisis_screen=CrisisScreen.load_ru_v2(),
            deadline_seconds=45.0,
        )
    )
    deps = TelegramDeps(
        strings=strings,
        get_onboarding_step=GetOnboardingStep(uow_factory, catalog),
        get_user_by_telegram_id=GetUserByTelegramId(uow_factory),
        accept_age=AcceptAgeConfirmation(uow_factory, ids, clock),
        grant_consent=GrantConsent(uow_factory, catalog, ids, clock),
        get_consent_document=GetConsentDocument(catalog),
        decode_incoming=decode,
        inline_compose=compose,
        record_inline_choice=RecordInlineChoice(
            RecordInlineChoicePorts(
                sink=sink,
                uow_factory=uow_factory,
                catalog=catalog,
                tone_catalog=StaticToneSuggestionCatalog(),
                clock=clock,
                ids=ids,
                pseudonymizer=pepper,
            )
        ),
        prepared_results=ValkeyPreparedResults(valkey, ttl_seconds=600),
        rule_sources=rule_sources,
        suggest_rule_from_decode=suggest_rule_from_decode,
        inline_queries=InlineQueryCoordinator(AsyncioSleeper(), debounce_seconds=0.0),
        revoke_all_consents=RevokeAllConsents(uow_factory, clock, reuse),
        delete_my_account=DeleteMyAccount(uow_factory, ids, pepper, clock, reuse),
        export_my_data=ExportMyData(uow_factory, clock),
        confirmation_tokens=ValkeyConfirmationTokens(valkey),
        create_contact=CreateContact(uow_factory, catalog, ids, clock),
        list_contacts=ListContacts(uow_factory, catalog),
        rename_contact=RenameContact(uow_factory, catalog),
        set_active_contact=SetActiveContact(uow_factory, catalog),
        propose_rule=ProposeRule(uow_factory, catalog, ids, clock),
        list_rules=ListRules(uow_factory, catalog),
        archive_rule=ArchiveRule(uow_factory, catalog, clock),
        list_suggestions=ListSuggestions(uow_factory, catalog),
        accept_suggestion=AcceptSuggestion(uow_factory, catalog, ids, clock),
        dismiss_suggestion=DismissSuggestion(uow_factory, catalog, clock),
        dialog_state=ValkeyDialogState(valkey, ttl_seconds=600),
        clock=clock,
        display_timezone=ZoneInfo("Europe/Moscow"),
        deduplicator=ValkeyUpdateDeduplicator(valkey, ttl_seconds=60),
        rate_limiter=ValkeyRateLimiter(valkey, limit=30, window_seconds=60, key_prefix="tg:rl"),
        pseudonymizer=pepper,
        monotonic=monotonic,
        draft_min_interval_ms=50,
        inline_cache_seconds=30,
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
        if str(key).startswith("tg:prepared:") and isinstance(value, str):
            raw = base64.b64decode(value)
            for marker in markers:
                assert marker.encode("utf-8") not in raw

    await valkey.flushdb()
    await close_client(valkey)


_LABEL_SENTINEL = "SENTINEL_CONTACT_LABEL_0009_INT"
_LABEL_USER_ID = 5550009


def _contact_privacy_lifecycle(
    settings: Settings,
    uow_factory: SqlAlchemyUnitOfWorkFactory,
    valkey: Redis,
) -> tuple[TelegramLifecycle, Bot, PackageConsentCatalog, FakeTextGenerator]:
    catalog = PackageConsentCatalog()
    clock = SystemClock()
    monotonic = SystemMonotonicClock()
    ids = Uuid7IdGenerator()
    pepper = HmacPseudonymizer(settings.pseudonym_pepper_bytes())
    strings = load_ru_strings()
    sink = UnitOfWorkUsageEventSink(uow_factory)
    generator = FakeTextGenerator()
    decode = DecodeIncoming(
        DecodeIncomingPorts(
            uow_factory=uow_factory,
            catalog=catalog,
            generator=generator,
            guard=ValkeyConcurrencyGuard(valkey),
            quota=ValkeyRateLimiter(
                valkey, limit=20, window_seconds=3600, key_prefix="tg:decode:quota"
            ),
            sink=sink,
            clock=clock,
            monotonic=monotonic,
            ids=ids,
            pseudonymizer=pepper,
            crisis_screen=CrisisScreen.load_ru_v2(),
            deadline_seconds=45.0,
        )
    )
    reuse = make_inline_reuse(monotonic, ttl_seconds=30.0)
    compose = InlineCompose(
        InlineComposePorts(
            uow_factory=uow_factory,
            catalog=catalog,
            generator=generator,
            quota=ValkeyRateLimiter(
                valkey, limit=30, window_seconds=3600, key_prefix="tg:inline:quota"
            ),
            sink=sink,
            clock=clock,
            monotonic=monotonic,
            ids=ids,
            pseudonymizer=pepper,
            crisis_screen=CrisisScreen.load_ru_v2(),
            reuse=reuse,
            min_chars=8,
            deadline_seconds=8.0,
            intent_prefixes=help_say_intent_prefixes(strings),
        )
    )
    rule_sources = ValkeyRuleSources(valkey, ttl_seconds=600)
    suggest_rule_from_decode = SuggestRuleFromDecode(
        SuggestRuleFromDecodePorts(
            uow_factory=uow_factory,
            catalog=catalog,
            rule_sources=rule_sources,
            generator=generator,
            quota=ValkeyRateLimiter(
                valkey, limit=10, window_seconds=3600, key_prefix="tg:suggest:quota"
            ),
            sink=sink,
            clock=clock,
            monotonic=monotonic,
            ids=ids,
            pseudonymizer=pepper,
            crisis_screen=CrisisScreen.load_ru_v2(),
            deadline_seconds=45.0,
        )
    )
    deps = TelegramDeps(
        strings=strings,
        get_onboarding_step=GetOnboardingStep(uow_factory, catalog),
        get_user_by_telegram_id=GetUserByTelegramId(uow_factory),
        accept_age=AcceptAgeConfirmation(uow_factory, ids, clock),
        grant_consent=GrantConsent(uow_factory, catalog, ids, clock),
        get_consent_document=GetConsentDocument(catalog),
        decode_incoming=decode,
        inline_compose=compose,
        record_inline_choice=RecordInlineChoice(
            RecordInlineChoicePorts(
                sink=sink,
                uow_factory=uow_factory,
                catalog=catalog,
                tone_catalog=StaticToneSuggestionCatalog(),
                clock=clock,
                ids=ids,
                pseudonymizer=pepper,
            )
        ),
        prepared_results=ValkeyPreparedResults(valkey, ttl_seconds=600),
        rule_sources=rule_sources,
        suggest_rule_from_decode=suggest_rule_from_decode,
        inline_queries=InlineQueryCoordinator(AsyncioSleeper(), debounce_seconds=0.0),
        revoke_all_consents=RevokeAllConsents(uow_factory, clock, reuse),
        delete_my_account=DeleteMyAccount(uow_factory, ids, pepper, clock, reuse),
        export_my_data=ExportMyData(uow_factory, clock),
        confirmation_tokens=ValkeyConfirmationTokens(valkey),
        create_contact=CreateContact(uow_factory, catalog, ids, clock),
        list_contacts=ListContacts(uow_factory, catalog),
        rename_contact=RenameContact(uow_factory, catalog),
        set_active_contact=SetActiveContact(uow_factory, catalog),
        propose_rule=ProposeRule(uow_factory, catalog, ids, clock),
        list_rules=ListRules(uow_factory, catalog),
        archive_rule=ArchiveRule(uow_factory, catalog, clock),
        list_suggestions=ListSuggestions(uow_factory, catalog),
        accept_suggestion=AcceptSuggestion(uow_factory, catalog, ids, clock),
        dismiss_suggestion=DismissSuggestion(uow_factory, catalog, clock),
        dialog_state=ValkeyDialogState(valkey, ttl_seconds=600),
        clock=clock,
        display_timezone=ZoneInfo("Europe/Moscow"),
        deduplicator=ValkeyUpdateDeduplicator(valkey, ttl_seconds=60),
        rate_limiter=ValkeyRateLimiter(valkey, limit=30, window_seconds=60, key_prefix="tg:rl"),
        pseudonymizer=pepper,
        monotonic=monotonic,
        draft_min_interval_ms=50,
        inline_cache_seconds=30,
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
    return lifecycle, bot, catalog, generator


@pytest.mark.integration
async def test_contact_label_sentinel_only_as_ciphertext(
    settings: Settings,
    engine: AsyncEngine,
    uow_factory_postgres: SqlAlchemyUnitOfWorkFactory,
    capture_log_events: Callable[[], list[dict[str, Any]]],
) -> None:
    uow_factory = uow_factory_postgres
    valkey = create_client(
        make_settings(
            database_url=settings.database_url.get_secret_value(),
            valkey_url=_db15_url(settings.valkey_url.get_secret_value()),
        )
    )
    await valkey.flushdb()
    lifecycle, bot, catalog, generator = _contact_privacy_lifecycle(settings, uow_factory, valkey)
    uid = _LABEL_USER_ID

    def _msg(update_id: int, text: str) -> Update:
        return Update(
            update_id=update_id,
            message=Message(
                message_id=update_id,
                date=_NOW,
                chat=Chat(id=uid, type="private"),
                from_user=User(id=uid, is_bot=False, first_name="A"),
                text=text,
            ),
        )

    def _cb(update_id: int, data: str) -> Update:
        return Update(
            update_id=update_id,
            callback_query=CallbackQuery(
                id=str(update_id),
                from_user=User(id=uid, is_bot=False, first_name="A"),
                chat_instance="x",
                data=data,
                message=Message(
                    message_id=1,
                    date=_NOW,
                    chat=Chat(id=uid, type="private"),
                    from_user=User(id=uid, is_bot=False, first_name="A"),
                    text="p",
                ),
            ),
        )

    await lifecycle.dispatcher.feed_update(bot, _msg(1, "/start"))
    await lifecycle.dispatcher.feed_update(bot, _cb(2, "age:y"))
    pd = catalog.current_document(ConsentKind.PERSONAL_DATA)
    sc = catalog.current_document(ConsentKind.SPECIAL_CATEGORY)
    await lifecycle.dispatcher.feed_update(bot, _cb(3, f"cg:personal_data:{pd.version}:y"))
    await lifecycle.dispatcher.feed_update(bot, _cb(4, f"cg:special_category:{sc.version}:y"))
    await lifecycle.dispatcher.feed_update(bot, _cb(5, "ct:n"))
    await lifecycle.dispatcher.feed_update(bot, _cb(6, "ct:rel:friend"))
    keys_during = [key async for key in valkey.scan_iter(match="tg:dialog:*")]
    for key in keys_during:
        value = await valkey.get(key)
        rendered = "" if value is None else str(value)
        assert _LABEL_SENTINEL not in key
        assert _LABEL_SENTINEL not in rendered
    await lifecycle.dispatcher.feed_update(bot, _msg(7, _LABEL_SENTINEL))
    assert generator.decode_stream_calls == []
    async with engine.connect() as conn:
        count = (await conn.execute(text("SELECT count(*) FROM usage_events"))).scalar_one()
        assert count == 0
        await _assert_no_markers_in_text_columns(conn, (_LABEL_SENTINEL,))
        cipher_rows = (await conn.execute(text("SELECT label_ciphertext FROM contacts"))).all()
        assert cipher_rows
        assert all(_LABEL_SENTINEL.encode() not in bytes(row[0]) for row in cipher_rows)
    async with uow_factory() as uow:
        user = await uow.users.get_by_telegram_id(TelegramUserId(uid))
        assert user is not None
        contacts = await uow.contacts.list_for_owner(user.id)
        assert len(contacts) == 1
        assert contacts[0].label.value == _LABEL_SENTINEL
    keys = [key async for key in valkey.scan_iter(match="*")]
    for key in keys:
        assert _LABEL_SENTINEL not in str(key)
        value = await valkey.get(key)
        rendered = "" if value is None else str(value)
        assert _LABEL_SENTINEL not in rendered
    blob = " ".join(str(event) for event in capture_log_events())
    assert _LABEL_SENTINEL not in blob
    await valkey.flushdb()
    await close_client(valkey)


_RULE_SENTINEL = "SENTINEL_RULE_TEXT_0009_INT"
_RULE_USER_ID = 5550010


@pytest.mark.integration
async def test_rule_text_sentinel_only_as_ciphertext(
    settings: Settings,
    engine: AsyncEngine,
    uow_factory_postgres: SqlAlchemyUnitOfWorkFactory,
    capture_log_events: Callable[[], list[dict[str, Any]]],
) -> None:
    uow_factory = uow_factory_postgres
    valkey = create_client(
        make_settings(
            database_url=settings.database_url.get_secret_value(),
            valkey_url=_db15_url(settings.valkey_url.get_secret_value()),
        )
    )
    await valkey.flushdb()
    lifecycle, bot, catalog, generator = _contact_privacy_lifecycle(settings, uow_factory, valkey)
    uid = _RULE_USER_ID
    await lifecycle.dispatcher.feed_update(bot, _uid_message(uid, 1, "/start"))
    await lifecycle.dispatcher.feed_update(bot, _uid_callback(uid, 2, "age:y"))
    pd = catalog.current_document(ConsentKind.PERSONAL_DATA)
    sc = catalog.current_document(ConsentKind.SPECIAL_CATEGORY)
    await lifecycle.dispatcher.feed_update(
        bot, _uid_callback(uid, 3, f"cg:personal_data:{pd.version}:y")
    )
    await lifecycle.dispatcher.feed_update(
        bot, _uid_callback(uid, 4, f"cg:special_category:{sc.version}:y")
    )
    await lifecycle.dispatcher.feed_update(bot, _uid_callback(uid, 5, "ct:n"))
    await lifecycle.dispatcher.feed_update(bot, _uid_callback(uid, 6, "ct:rel:friend"))
    await lifecycle.dispatcher.feed_update(bot, _uid_message(uid, 7, "Sam"))
    await lifecycle.dispatcher.feed_update(bot, _uid_callback(uid, 8, "ru:n"))
    await lifecycle.dispatcher.feed_update(bot, _uid_callback(uid, 9, "ru:cat:other"))
    dialog_keys = [key async for key in valkey.scan_iter(match="tg:dialog:*")]
    for key in dialog_keys:
        value = await valkey.get(key)
        raw = "" if value is None else (value if isinstance(value, str) else value.decode())
        assert _RULE_SENTINEL not in key and _RULE_SENTINEL not in raw
        payload = json.loads(raw) if raw else {}
        assert set(payload) <= {"step", "contact_id", "relationship", "category"}
    await lifecycle.dispatcher.feed_update(bot, _uid_message(uid, 10, _RULE_SENTINEL))
    assert generator.decode_stream_calls == []
    async with engine.connect() as conn:
        count = (await conn.execute(text("SELECT count(*) FROM usage_events"))).scalar_one()
        assert count == 0
        await _assert_no_markers_in_text_columns(conn, (_RULE_SENTINEL,))
        cipher_rows = (await conn.execute(text("SELECT text_ciphertext FROM rule_revisions"))).all()
        assert cipher_rows
        assert all(_RULE_SENTINEL.encode() not in bytes(row[0]) for row in cipher_rows)
    async with uow_factory() as uow:
        user = await uow.users.get_by_telegram_id(TelegramUserId(uid))
        assert user is not None
        contacts = await uow.contacts.list_for_owner(user.id)
        rules = await uow.rules.list_for_scope(ContactScope(contact_id=contacts[0].id))
        assert rules[0].revisions[-1].text.value == _RULE_SENTINEL
    await _assert_valkey_without_sentinel(valkey, _RULE_SENTINEL)
    blob = " ".join(str(event) for event in capture_log_events())
    assert _RULE_SENTINEL not in blob
    await valkey.flushdb()
    await close_client(valkey)
