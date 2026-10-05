"""Telegram channel tests for decode «Сделать правилом» flow."""

from __future__ import annotations

import pytest
from aiogram import Bot
from tests.fakes.consent_catalog import FakeConsentCatalog
from tests.fakes.generation import FakeTextGenerator
from tests.fakes.telegram_deps import TelegramTestDeps, make_telegram_deps
from tests.fakes.telegram_session import FakeTelegramSession
from tests.fakes.uow import InMemoryUnitOfWorkFactory
from tests.unit.adapters.channels.telegram.test_suggestions import (
    _add_contact,
    _callback,
    _onboard,
    _sent_texts,
    _settings,
)

from svoi_pravila.adapters.channels.telegram.factory import build_telegram_lifecycle
from svoi_pravila.adapters.channels.telegram.handlers.helpers import FEATURE_CALLBACK_PREFIXES
from svoi_pravila.adapters.channels.telegram.localization import load_ru_strings
from svoi_pravila.adapters.channels.telegram.presenters import render_decode_completed
from svoi_pravila.application.ports.generation import (
    DecodeCompleted,
    DecodeResult,
    GenerationMeta,
    SafetyVerdict,
    SuggestRuleResult,
    SuggestRuleVerdict,
    TokenUsage,
    Variant,
)
from svoi_pravila.application.rule_source import RuleSourcePayload, rule_source_callback_data
from svoi_pravila.application.use_cases.suggest_rule_from_decode import RULE_SOURCE_PURPOSE
from svoi_pravila.domain.enums import Firmness, SuggestionStatus
from svoi_pravila.domain.ids import TelegramUserId


def _ok_completed() -> DecodeCompleted:
    meta = GenerationMeta(
        model="fake",
        prompt_version="decode@v1",
        latency_ms=1,
        attempts=1,
        usage=TokenUsage(1, 1, 0),
    )
    return DecodeCompleted(
        analysis="analysis",
        result=DecodeResult(
            hypotheses=("h",),
            underlying_request="r",
            variants=(
                Variant("g", Firmness.GENTLE),
                Variant("b", Firmness.BALANCED),
                Variant("f", Firmness.FIRM),
            ),
            applied_rule_indexes=(),
            safety=SafetyVerdict.OK,
            meta=meta,
        ),
    )


@pytest.mark.unit
def test_feature_callback_registry_includes_sn() -> None:
    assert "sn" in FEATURE_CALLBACK_PREFIXES


@pytest.mark.unit
def test_make_rule_button_only_on_last_ok_variant() -> None:
    strings = load_ru_strings()
    messages = render_decode_completed(
        strings,
        _ok_completed(),
        copy_max=256,
        insert_queries=(None, None, None),
        make_rule_callback="sn:token",
    )
    variant_msgs = [m for m in messages if m[1] is not None]
    assert len(variant_msgs) == 3
    dumped = [str(markup.model_dump()) for _, markup in variant_msgs if markup is not None]
    assert all("Сделать правилом" not in text for text in dumped[:-1])
    assert "Сделать правилом" in dumped[-1]


@pytest.mark.unit
async def test_sn_callback_ok_and_sg_e_edit_flow() -> None:
    uow = InMemoryUnitOfWorkFactory()
    catalog = FakeConsentCatalog()
    deps = make_telegram_deps(TelegramTestDeps(uow=uow, catalog=catalog))
    session = FakeTelegramSession()
    bot = Bot(token="1:TEST", session=session)
    lifecycle = build_telegram_lifecycle(_settings(), deps, bot=bot)
    await _onboard(bot, lifecycle, 7101, catalog)
    await _add_contact(bot, lifecycle, 7101, "Мама")
    async with uow() as active:
        user = await active.users.get_by_telegram_id(TelegramUserId(7101))
        assert user is not None
        assert user.active_contact_id is not None
        contact_id = user.active_contact_id
    token = await deps.rule_sources.store(
        deps.pseudonymizer.pseudonymize(RULE_SOURCE_PURPOSE, "7101"),
        RuleSourcePayload(contact_id=contact_id, incoming_text="Давай без сарказма"),
    )
    session.requests.clear()
    await lifecycle.dispatcher.feed_update(
        bot, _callback(800, 7101, rule_source_callback_data(token))
    )
    assert any("Предлагаю правило" in text for text in _sent_texts(session))
    async with uow() as active:
        user = await active.users.get_by_telegram_id(TelegramUserId(7101))
        assert user is not None
        pending = await active.rule_suggestions.list_pending_for_contact(user.id, contact_id)
        assert len(pending) == 1
        suggestion_id = pending[0].id

    session.requests.clear()
    await lifecycle.dispatcher.feed_update(bot, _callback(801, 7101, f"sg:e:{suggestion_id}"))
    assert deps.strings.suggestion_decode_edit_prompt in _sent_texts(session)
    async with uow() as active:
        suggestion = await active.rule_suggestions.get(suggestion_id)
        assert suggestion is not None
        assert suggestion.status is SuggestionStatus.DISMISSED

    session.requests.clear()
    await lifecycle.dispatcher.feed_update(bot, _callback(802, 7101, f"sg:e:{suggestion_id}"))
    assert deps.strings.suggestion_already_decided in _sent_texts(session)
    session.requests.clear()
    await lifecycle.dispatcher.feed_update(bot, _callback(803, 7101, "sg:e:not-a-uuid"))
    assert deps.strings.suggestion_decode_edit_prompt not in _sent_texts(session)


@pytest.mark.unit
async def test_sn_callback_expired_none_crisis_quota() -> None:
    uow = InMemoryUnitOfWorkFactory()
    catalog = FakeConsentCatalog()
    none_gen = FakeTextGenerator()
    none_gen.suggest_rule_result = SuggestRuleResult(
        verdict=SuggestRuleVerdict.NONE,
        category=None,
        text=None,
        meta=GenerationMeta(
            model="fake",
            prompt_version="suggest_rule@v1",
            latency_ms=1,
            attempts=1,
            usage=TokenUsage(1, 1, 0),
        ),
    )
    deps = make_telegram_deps(
        TelegramTestDeps(uow=uow, catalog=catalog, generator=none_gen, suggest_quota_limit=1)
    )
    session = FakeTelegramSession()
    bot = Bot(token="1:TEST", session=session)
    lifecycle = build_telegram_lifecycle(_settings(), deps, bot=bot)
    await _onboard(bot, lifecycle, 7102, catalog)
    await _add_contact(bot, lifecycle, 7102, "Мама")
    async with uow() as active:
        user = await active.users.get_by_telegram_id(TelegramUserId(7102))
        assert user is not None
        assert user.active_contact_id is not None
        contact_id = user.active_contact_id
    pseudo = deps.pseudonymizer.pseudonymize(RULE_SOURCE_PURPOSE, "7102")

    session.requests.clear()
    await lifecycle.dispatcher.feed_update(bot, _callback(900, 7102, "sn:not-a-real-token"))
    assert deps.strings.suggestion_decode_expired in _sent_texts(session)

    token_none = await deps.rule_sources.store(
        pseudo, RuleSourcePayload(contact_id=contact_id, incoming_text="просто ок")
    )
    session.requests.clear()
    await lifecycle.dispatcher.feed_update(
        bot, _callback(901, 7102, rule_source_callback_data(token_none))
    )
    assert deps.strings.suggestion_decode_none in _sent_texts(session)

    token_crisis = await deps.rule_sources.store(
        pseudo,
        RuleSourcePayload(contact_id=contact_id, incoming_text="Я хочу покончить с собой"),
    )
    session.requests.clear()
    await lifecycle.dispatcher.feed_update(
        bot, _callback(902, 7102, rule_source_callback_data(token_crisis))
    )
    assert deps.strings.suggestion_decode_crisis in _sent_texts(session)

    deps_q = make_telegram_deps(TelegramTestDeps(uow=uow, catalog=catalog, suggest_quota_limit=0))
    session_q = FakeTelegramSession()
    bot_q = Bot(token="1:TEST", session=session_q)
    lifecycle_q = build_telegram_lifecycle(_settings(), deps_q, bot=bot_q)
    token_q = await deps_q.rule_sources.store(
        deps_q.pseudonymizer.pseudonymize(RULE_SOURCE_PURPOSE, "7102"),
        RuleSourcePayload(contact_id=contact_id, incoming_text="квота"),
    )
    session_q.requests.clear()
    await lifecycle_q.dispatcher.feed_update(
        bot_q, _callback(903, 7102, rule_source_callback_data(token_q))
    )
    assert deps_q.strings.suggestion_decode_quota in _sent_texts(session_q)
