"""Inline query, prepared tokens, chosen_inline_result, and help deep-link."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
from aiogram import Bot
from aiogram.methods import AnswerInlineQuery, SendMessage
from aiogram.types import (
    CallbackQuery,
    Chat,
    ChosenInlineResult,
    InlineKeyboardMarkup,
    InlineQuery,
    InlineQueryResultArticle,
    InputTextMessageContent,
    Message,
    SwitchInlineQueryChosenChat,
    Update,
    User,
)
from tests.factories import make_settings
from tests.fakes.consent_catalog import FakeConsentCatalog
from tests.fakes.generation import FakeTextGenerator
from tests.fakes.prepared import FakePreparedResults
from tests.fakes.sleeper import GateSleeper
from tests.fakes.telegram_deps import TelegramTestDeps, make_telegram_deps
from tests.fakes.telegram_session import FakeTelegramSession
from tests.fakes.uow import InMemoryUnitOfWorkFactory
from tests.fakes.usage_sink import RecordingUsageEventSink

from svoi_pravila.adapters.channels.telegram.factory import build_telegram_lifecycle
from svoi_pravila.adapters.channels.telegram.lifecycle import ALLOWED_UPDATES, TelegramLifecycle
from svoi_pravila.adapters.channels.telegram.localization import (
    help_say_intent_prefixes,
    render_help,
)
from svoi_pravila.application.inline_result_ref import encode_inline_result_ref
from svoi_pravila.application.ports.generation import (
    GenerationMeta,
    HelpSayResult,
    SafetyVerdict,
    SoftenResult,
    TokenUsage,
    Variant,
)
from svoi_pravila.application.ports.prepared_results import PreparedVariant
from svoi_pravila.config import Environment, Settings, TelegramUpdatesMode
from svoi_pravila.domain.enums import ConsentKind, Firmness, UsageEventKind, UsageScenario

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _settings() -> Settings:
    return make_settings(
        environment=Environment.LOCAL,
        telegram_updates_mode=TelegramUpdatesMode.POLLING,
        telegram_bot_token="1:TEST",
    )


def _text_update(update_id: int, user_id: int, text: str) -> Update:
    return Update(
        update_id=update_id,
        message=Message(
            message_id=update_id,
            date=_NOW,
            chat=Chat(id=user_id, type="private"),
            from_user=User(id=user_id, is_bot=False, first_name="A"),
            text=text,
        ),
    )


def _callback(update_id: int, user_id: int, data: str) -> Update:
    return Update(
        update_id=update_id,
        callback_query=CallbackQuery(
            id=str(update_id),
            from_user=User(id=user_id, is_bot=False, first_name="A"),
            chat_instance="x",
            data=data,
            message=Message(
                message_id=1,
                date=_NOW,
                chat=Chat(id=user_id, type="private"),
                from_user=User(id=user_id, is_bot=False, first_name="A"),
                text="p",
            ),
        ),
    )


def _inline(update_id: int, user_id: int, query: str) -> Update:
    return Update(
        update_id=update_id,
        inline_query=InlineQuery(
            id=str(update_id),
            from_user=User(id=user_id, is_bot=False, first_name="A"),
            query=query,
            offset="",
        ),
    )


def _chosen(update_id: int, user_id: int, result_id: str, query: str) -> Update:
    return Update(
        update_id=update_id,
        chosen_inline_result=ChosenInlineResult(
            result_id=result_id,
            from_user=User(id=user_id, is_bot=False, first_name="A"),
            query=query,
        ),
    )


async def _onboard(
    bot: Bot, lifecycle: TelegramLifecycle, user_id: int, catalog: FakeConsentCatalog
) -> None:
    start = 2000 + user_id
    dispatcher = lifecycle.dispatcher
    await dispatcher.feed_update(bot, _text_update(start, user_id, "/start"))
    await dispatcher.feed_update(bot, _callback(start + 1, user_id, "age:y"))
    pd = catalog.current_requirement().for_kind(ConsentKind.PERSONAL_DATA).version
    sc = catalog.current_requirement().for_kind(ConsentKind.SPECIAL_CATEGORY).version
    await dispatcher.feed_update(bot, _callback(start + 2, user_id, f"cg:personal_data:{pd}:y"))
    await dispatcher.feed_update(bot, _callback(start + 3, user_id, f"cg:special_category:{sc}:y"))


@pytest.mark.unit
def test_allowed_updates_include_inline() -> None:
    assert "inline_query" in ALLOWED_UPDATES
    assert "chosen_inline_result" in ALLOWED_UPDATES


@pytest.mark.unit
async def test_help_and_start_help_include_prefixes() -> None:
    catalog = FakeConsentCatalog()
    deps = make_telegram_deps(TelegramTestDeps(catalog=catalog))
    session = FakeTelegramSession()
    bot = Bot(token="1:TEST", session=session)
    lifecycle = build_telegram_lifecycle(_settings(), deps, bot=bot)
    await lifecycle.dispatcher.feed_update(bot, _text_update(1, 11, "/help"))
    await lifecycle.dispatcher.feed_update(bot, _text_update(2, 11, "/start help"))
    bodies = [req.text for req in session.requests if isinstance(req, SendMessage)]
    expected = render_help(deps.strings)
    assert expected in bodies
    for prefix, _intent in help_say_intent_prefixes(deps.strings):
        assert prefix in expected


@pytest.mark.unit
async def test_inline_not_onboarded_gets_setup_button() -> None:
    deps = make_telegram_deps()
    session = FakeTelegramSession()
    bot = Bot(token="1:TEST", session=session)
    lifecycle = build_telegram_lifecycle(_settings(), deps, bot=bot)
    await lifecycle.dispatcher.feed_update(bot, _inline(1, 12, "long enough draft"))
    await deps.inline_queries.drain()
    answers = [req for req in session.requests if isinstance(req, AnswerInlineQuery)]
    assert len(answers) == 1
    assert answers[0].results == []
    assert answers[0].is_personal is True
    assert answers[0].cache_time == 30
    assert answers[0].button is not None
    assert answers[0].button.text == deps.strings.inline_button_finish_setup
    assert answers[0].button.start_parameter == "start"


@pytest.mark.unit
async def test_inline_too_short_help_button() -> None:
    catalog = FakeConsentCatalog()
    uow = InMemoryUnitOfWorkFactory()
    deps = make_telegram_deps(TelegramTestDeps(uow=uow, catalog=catalog))
    session = FakeTelegramSession()
    bot = Bot(token="1:TEST", session=session)
    lifecycle = build_telegram_lifecycle(_settings(), deps, bot=bot)
    await _onboard(bot, lifecycle, 13, catalog)
    await lifecycle.dispatcher.feed_update(bot, _inline(10, 13, "hi"))
    await deps.inline_queries.drain()
    answers = [req for req in session.requests if isinstance(req, AnswerInlineQuery)]
    assert answers[-1].results == []
    assert answers[-1].button is not None
    assert answers[-1].button.start_parameter == "help"


@pytest.mark.unit
async def test_inline_compose_articles_and_choice() -> None:
    catalog = FakeConsentCatalog()
    uow = InMemoryUnitOfWorkFactory()
    sink = RecordingUsageEventSink()
    deps = make_telegram_deps(
        TelegramTestDeps(uow=uow, catalog=catalog, sink=sink, generator=FakeTextGenerator())
    )
    session = FakeTelegramSession()
    bot = Bot(token="1:TEST", session=session)
    lifecycle = build_telegram_lifecycle(_settings(), deps, bot=bot)
    await _onboard(bot, lifecycle, 14, catalog)
    await lifecycle.dispatcher.feed_update(bot, _inline(20, 14, "please leave quietly"))
    await deps.inline_queries.drain()
    answers = [req for req in session.requests if isinstance(req, AnswerInlineQuery)]
    assert answers[-1].is_personal is True
    assert len(answers[-1].results) == 2
    first = answers[-1].results[0]
    assert isinstance(first, InlineQueryResultArticle)
    content = first.input_message_content
    assert isinstance(content, InputTextMessageContent)
    assert first.id == encode_inline_result_ref(UsageScenario.SOFTEN, Firmness.GENTLE)
    assert content.message_text == "softened-a"
    generations = [event for event in sink.events if event.event_kind is UsageEventKind.GENERATION]
    assert len(generations) == 1
    await lifecycle.dispatcher.feed_update(bot, _chosen(21, 14, first.id, "please leave quietly"))
    chosen = [event for event in sink.events if event.event_kind is UsageEventKind.RESULT_CHOSEN]
    assert len(chosen) == 1
    assert chosen[0].variant_firmness is Firmness.GENTLE


@pytest.mark.unit
async def test_prepared_token_answers_without_generation() -> None:
    catalog = FakeConsentCatalog()
    uow = InMemoryUnitOfWorkFactory()
    sink = RecordingUsageEventSink()
    prepared = FakePreparedResults()
    generator = FakeTextGenerator()
    deps = make_telegram_deps(
        TelegramTestDeps(
            uow=uow,
            catalog=catalog,
            sink=sink,
            generator=generator,
            prepared=prepared,
        )
    )
    session = FakeTelegramSession()
    bot = Bot(token="1:TEST", session=session)
    lifecycle = build_telegram_lifecycle(_settings(), deps, bot=bot)
    await _onboard(bot, lifecycle, 15, catalog)
    token = await prepared.store(
        deps.pseudonymizer.pseudonymize("prepared", "15"),
        PreparedVariant(firmness=Firmness.BALANCED, text="insert-me"),
    )
    await lifecycle.dispatcher.feed_update(bot, _inline(30, 15, token))
    await deps.inline_queries.drain()
    assert generator.soften_calls == []
    answers = [req for req in session.requests if isinstance(req, AnswerInlineQuery)]
    assert len(answers[-1].results) == 1
    article = answers[-1].results[0]
    assert isinstance(article, InlineQueryResultArticle)
    content = article.input_message_content
    assert isinstance(content, InputTextMessageContent)
    assert content.message_text == "insert-me"
    generations = [event for event in sink.events if event.event_kind is UsageEventKind.GENERATION]
    assert generations == []
    await lifecycle.dispatcher.feed_update(bot, _chosen(31, 15, answers[-1].results[0].id, token))
    assert token not in prepared.items


@pytest.mark.unit
async def test_inline_debounce_cancels_before_provider() -> None:
    catalog = FakeConsentCatalog()
    uow = InMemoryUnitOfWorkFactory()
    sleeper = GateSleeper()
    generator = FakeTextGenerator()
    deps = make_telegram_deps(
        TelegramTestDeps(
            uow=uow,
            catalog=catalog,
            generator=generator,
            sleeper=sleeper,
            debounce_seconds=0.6,
        )
    )
    session = FakeTelegramSession()
    bot = Bot(token="1:TEST", session=session)
    lifecycle = build_telegram_lifecycle(_settings(), deps, bot=bot)
    await _onboard(bot, lifecycle, 16, catalog)
    await lifecycle.dispatcher.feed_update(bot, _inline(40, 16, "first draft here"))
    await sleeper.entered.wait()
    sleeper.entered.clear()
    await lifecycle.dispatcher.feed_update(bot, _inline(41, 16, "second draft here"))
    await sleeper.entered.wait()
    sleeper.release.set()
    await deps.inline_queries.drain()
    assert len(generator.soften_calls) == 1
    assert generator.soften_calls[0].draft == "second draft here"


@pytest.mark.unit
async def test_inline_in_flight_generation_not_cancelled() -> None:
    catalog = FakeConsentCatalog()
    uow = InMemoryUnitOfWorkFactory()
    block = asyncio.Event()
    generator = FakeTextGenerator()
    generator.soften_block = block
    sink = RecordingUsageEventSink()
    deps = make_telegram_deps(
        TelegramTestDeps(
            uow=uow,
            catalog=catalog,
            generator=generator,
            sink=sink,
            debounce_seconds=0.0,
        )
    )
    session = FakeTelegramSession()
    bot = Bot(token="1:TEST", session=session)
    lifecycle = build_telegram_lifecycle(_settings(), deps, bot=bot)
    await _onboard(bot, lifecycle, 17, catalog)
    await lifecycle.dispatcher.feed_update(bot, _inline(50, 17, "first long draft"))
    await generator.soften_started.wait()
    await lifecycle.dispatcher.feed_update(bot, _inline(51, 17, "second long draft"))
    block.set()
    await deps.inline_queries.drain()
    assert len(generator.soften_calls) == 2
    generations = [event for event in sink.events if event.event_kind is UsageEventKind.GENERATION]
    assert len(generations) == 2
    answers = [req for req in session.requests if isinstance(req, AnswerInlineQuery)]
    assert len(answers) == 1


@pytest.mark.unit
async def test_decode_variant_has_insert_chat_button() -> None:
    catalog = FakeConsentCatalog()
    uow = InMemoryUnitOfWorkFactory()
    prepared = FakePreparedResults()
    deps = make_telegram_deps(TelegramTestDeps(uow=uow, catalog=catalog, prepared=prepared))
    session = FakeTelegramSession()
    bot = Bot(token="1:TEST", session=session)
    lifecycle = build_telegram_lifecycle(_settings(), deps, bot=bot)
    await _onboard(bot, lifecycle, 18, catalog)
    await lifecycle.dispatcher.feed_update(bot, _text_update(60, 18, "incoming text here"))
    sends = [req for req in session.requests if isinstance(req, SendMessage) and req.reply_markup]
    switch = None
    for send in sends:
        markup = send.reply_markup
        if markup is None:
            continue
        if isinstance(markup, InlineKeyboardMarkup):
            for row in markup.inline_keyboard:
                for button in row:
                    if button.switch_inline_query_chosen_chat is not None:
                        switch = button.switch_inline_query_chosen_chat
    assert isinstance(switch, SwitchInlineQueryChosenChat)
    assert switch.allow_user_chats is True
    assert switch.allow_bot_chats is False
    assert switch.allow_group_chats is True
    assert switch.allow_channel_chats is True
    assert switch.query is not None
    assert prepared.store_calls >= 1


@pytest.mark.unit
async def test_help_say_inline_articles() -> None:
    catalog = FakeConsentCatalog()
    uow = InMemoryUnitOfWorkFactory()
    generator = FakeTextGenerator(
        help_say_result=HelpSayResult(
            variants=(Variant(text="n", firmness=Firmness.GENTLE),),
            applied_rule_indexes=(),
            safety=SafetyVerdict.OK,
            meta=GenerationMeta(
                model="fake",
                prompt_version="help_say@v1",
                latency_ms=1,
                attempts=1,
                usage=TokenUsage(),
            ),
        )
    )
    deps = make_telegram_deps(TelegramTestDeps(uow=uow, catalog=catalog, generator=generator))
    session = FakeTelegramSession()
    bot = Bot(token="1:TEST", session=session)
    lifecycle = build_telegram_lifecycle(_settings(), deps, bot=bot)
    await _onboard(bot, lifecycle, 19, catalog)
    await lifecycle.dispatcher.feed_update(bot, _inline(70, 19, "извинись: I was late to dinner"))
    await deps.inline_queries.drain()
    answers = [req for req in session.requests if isinstance(req, AnswerInlineQuery)]
    article = answers[-1].results[0]
    assert isinstance(article, InlineQueryResultArticle)
    assert article.id.startswith("s:help_say:")


@pytest.mark.unit
async def test_inline_empty_variants_help_button() -> None:
    catalog = FakeConsentCatalog()
    uow = InMemoryUnitOfWorkFactory()
    generator = FakeTextGenerator(
        soften_result=SoftenResult(
            variants=(),
            applied_rule_indexes=(),
            safety=SafetyVerdict.OK,
            meta=GenerationMeta(
                model="fake",
                prompt_version="soften@v1",
                latency_ms=1,
                attempts=1,
                usage=TokenUsage(),
            ),
        )
    )
    deps = make_telegram_deps(TelegramTestDeps(uow=uow, catalog=catalog, generator=generator))
    session = FakeTelegramSession()
    bot = Bot(token="1:TEST", session=session)
    lifecycle = build_telegram_lifecycle(_settings(), deps, bot=bot)
    await _onboard(bot, lifecycle, 22, catalog)
    await lifecycle.dispatcher.feed_update(bot, _inline(90, 22, "long enough draft"))
    await deps.inline_queries.drain()
    answers = [req for req in session.requests if isinstance(req, AnswerInlineQuery)]
    assert answers[-1].results == []
    assert answers[-1].button is not None
    await deps.inline_queries.drain()


@pytest.mark.unit
async def test_chosen_invalid_ref_is_ignored() -> None:
    catalog = FakeConsentCatalog()
    uow = InMemoryUnitOfWorkFactory()
    sink = RecordingUsageEventSink()
    deps = make_telegram_deps(TelegramTestDeps(uow=uow, catalog=catalog, sink=sink))
    session = FakeTelegramSession()
    bot = Bot(token="1:TEST", session=session)
    lifecycle = build_telegram_lifecycle(_settings(), deps, bot=bot)
    await lifecycle.dispatcher.feed_update(bot, _chosen(80, 21, "not-a-ref", "query text"))
    assert [e for e in sink.events if e.event_kind is UsageEventKind.RESULT_CHOSEN] == []
