"""Inline query, prepared tokens, chosen_inline_result, and help deep-link."""

from __future__ import annotations

import asyncio
from dataclasses import replace
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

from svoi_pravila.adapters.channels.telegram.deps import TelegramDeps
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
from svoi_pravila.application.prepared_ref import PREPARED_REF_PREFIX, is_prepared_ref
from svoi_pravila.application.use_cases.inline_compose import (
    InlineCompose,
    InlineComposeCommand,
    InlineComposeResult,
)
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


async def _await_inline(deps: TelegramDeps) -> None:
    pending = [task for task in deps.inline_queries.tasks if not task.done()]
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


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
    await _await_inline(deps)
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
    await _await_inline(deps)
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
    await _await_inline(deps)
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
    await _await_inline(deps)
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
    await lifecycle.shutdown()
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
    assert [call.draft for call in generator.soften_calls] == ["first long draft"]
    await lifecycle.dispatcher.feed_update(bot, _inline(51, 17, "second long draft"))
    assert [call.draft for call in generator.soften_calls] == ["first long draft"]
    block.set()
    await _await_inline(deps)
    assert [call.draft for call in generator.soften_calls] == [
        "first long draft",
        "second long draft",
    ]
    generations = [event for event in sink.events if event.event_kind is UsageEventKind.GENERATION]
    assert len(generations) == 2
    answers = [req for req in session.requests if isinstance(req, AnswerInlineQuery)]
    assert len(answers) == 1
    assert answers[0].inline_query_id == "51"


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
    await _await_inline(deps)
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
    await _await_inline(deps)
    answers = [req for req in session.requests if isinstance(req, AnswerInlineQuery)]
    assert answers[-1].results == []
    assert answers[-1].button is not None


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


@pytest.mark.unit
async def test_newer_query_waits_for_in_flight_then_runs() -> None:
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
    await _onboard(bot, lifecycle, 24, catalog)
    await lifecycle.dispatcher.feed_update(bot, _inline(100, 24, "alpha draft waiting"))
    await generator.soften_started.wait()
    await lifecycle.dispatcher.feed_update(bot, _inline(101, 24, "beta draft waiting"))
    assert len(generator.soften_calls) == 1
    block.set()
    await _await_inline(deps)
    assert [call.draft for call in generator.soften_calls] == [
        "alpha draft waiting",
        "beta draft waiting",
    ]
    answers = [req for req in session.requests if isinstance(req, AnswerInlineQuery)]
    assert [item.inline_query_id for item in answers] == ["101"]
    assert len([e for e in sink.events if e.event_kind is UsageEventKind.GENERATION]) == 2


@pytest.mark.unit
async def test_third_query_supersedes_waiter_only_latest_runs() -> None:
    catalog = FakeConsentCatalog()
    uow = InMemoryUnitOfWorkFactory()
    block = asyncio.Event()
    generator = FakeTextGenerator()
    generator.soften_block = block
    deps = make_telegram_deps(
        TelegramTestDeps(
            uow=uow,
            catalog=catalog,
            generator=generator,
            debounce_seconds=0.0,
        )
    )
    session = FakeTelegramSession()
    bot = Bot(token="1:TEST", session=session)
    lifecycle = build_telegram_lifecycle(_settings(), deps, bot=bot)
    await _onboard(bot, lifecycle, 25, catalog)
    await lifecycle.dispatcher.feed_update(bot, _inline(110, 25, "first of three drafts"))
    await generator.soften_started.wait()
    await lifecycle.dispatcher.feed_update(bot, _inline(111, 25, "middle of three drafts"))
    await asyncio.sleep(0)
    await lifecycle.dispatcher.feed_update(bot, _inline(112, 25, "latest of three drafts"))
    assert [call.draft for call in generator.soften_calls] == ["first of three drafts"]
    block.set()
    await _await_inline(deps)
    assert [call.draft for call in generator.soften_calls] == [
        "first of three drafts",
        "latest of three drafts",
    ]
    answers = [req for req in session.requests if isinstance(req, AnswerInlineQuery)]
    assert [item.inline_query_id for item in answers] == ["112"]


@pytest.mark.unit
async def test_not_onboarded_in_flight_answers_only_current_query() -> None:
    block = asyncio.Event()
    started = asyncio.Event()
    base = make_telegram_deps()
    inner = base.inline_compose

    class _HoldingCompose(InlineCompose):
        def __init__(self, wrapped: InlineCompose) -> None:
            super().__init__(wrapped._ports)

        async def execute(self, command: InlineComposeCommand) -> InlineComposeResult:
            started.set()
            await block.wait()
            return await super().execute(command)

    deps = replace(base, inline_compose=_HoldingCompose(inner))
    session = FakeTelegramSession()
    bot = Bot(token="1:TEST", session=session)
    lifecycle = build_telegram_lifecycle(_settings(), deps, bot=bot)
    await lifecycle.dispatcher.feed_update(bot, _inline(120, 26, "first not onboarded"))
    await started.wait()
    await lifecycle.dispatcher.feed_update(bot, _inline(121, 26, "second not onboarded"))
    block.set()
    await _await_inline(deps)
    answers = [req for req in session.requests if isinstance(req, AnswerInlineQuery)]
    assert [item.inline_query_id for item in answers] == ["121"]
    assert answers[0].results == []
    assert answers[0].button is not None
    assert answers[0].button.start_parameter == "start"


@pytest.mark.unit
async def test_in_flight_error_does_not_answer_stale_query() -> None:
    block = asyncio.Event()
    started = asyncio.Event()
    catalog = FakeConsentCatalog()
    uow = InMemoryUnitOfWorkFactory()
    base = make_telegram_deps(TelegramTestDeps(uow=uow, catalog=catalog))
    inner = base.inline_compose

    class _HoldingCompose(InlineCompose):
        def __init__(self, wrapped: InlineCompose) -> None:
            super().__init__(wrapped._ports)

        async def execute(self, command: InlineComposeCommand) -> InlineComposeResult:
            started.set()
            await block.wait()
            return await super().execute(command)

    deps = replace(base, inline_compose=_HoldingCompose(inner))
    session = FakeTelegramSession()
    bot = Bot(token="1:TEST", session=session)
    lifecycle = build_telegram_lifecycle(_settings(), deps, bot=bot)
    await _onboard(bot, lifecycle, 32, catalog)
    await lifecycle.dispatcher.feed_update(bot, _inline(150, 32, "hi"))
    await started.wait()
    await lifecycle.dispatcher.feed_update(bot, _inline(151, 32, "ok draft here"))
    block.set()
    await _await_inline(deps)
    answers = [req for req in session.requests if isinstance(req, AnswerInlineQuery)]
    assert [item.inline_query_id for item in answers] == ["151"]


async def _assert_prepared_ref_empty(
    *,
    user_id: int,
    update_id: int,
    query: str,
    prepared: FakePreparedResults,
) -> None:
    catalog = FakeConsentCatalog()
    uow = InMemoryUnitOfWorkFactory()
    generator = FakeTextGenerator()
    deps = make_telegram_deps(
        TelegramTestDeps(
            uow=uow,
            catalog=catalog,
            generator=generator,
            prepared=prepared,
        )
    )
    session = FakeTelegramSession()
    bot = Bot(token="1:TEST", session=session)
    lifecycle = build_telegram_lifecycle(_settings(), deps, bot=bot)
    await _onboard(bot, lifecycle, user_id, catalog)
    assert is_prepared_ref(query)
    await lifecycle.dispatcher.feed_update(bot, _inline(update_id, user_id, query))
    await lifecycle.shutdown()
    assert generator.soften_calls == []
    answers = [req for req in session.requests if isinstance(req, AnswerInlineQuery)]
    assert len(answers) == 1
    assert answers[0].results == []
    assert answers[0].button is not None
    assert answers[0].button.start_parameter == "help"
    assert answers[0].is_personal is True


@pytest.mark.unit
async def test_prepared_ref_wrong_user_empty_help() -> None:
    prepared = FakePreparedResults()
    stored = await prepared.store(
        "other-user",
        PreparedVariant(firmness=Firmness.GENTLE, text="nope"),
    )
    await _assert_prepared_ref_empty(user_id=27, update_id=130, query=stored, prepared=prepared)


@pytest.mark.unit
async def test_prepared_ref_expired_empty_help() -> None:
    prepared = FakePreparedResults()
    stored = await prepared.store(
        "placeholder",
        PreparedVariant(firmness=Firmness.GENTLE, text="gone"),
    )
    prepared.expired.add(stored)
    await _assert_prepared_ref_empty(user_id=28, update_id=131, query=stored, prepared=prepared)


@pytest.mark.unit
async def test_prepared_ref_tampered_byte_empty_help() -> None:
    prepared = FakePreparedResults()
    stored = await prepared.store(
        "placeholder",
        PreparedVariant(firmness=Firmness.GENTLE, text="secret"),
    )
    flip = "A" if stored[-1] != "A" else "B"
    tampered = stored[:-1] + flip
    await _assert_prepared_ref_empty(user_id=29, update_id=132, query=tampered, prepared=prepared)


@pytest.mark.unit
async def test_prepared_ref_unknown_id_empty_help() -> None:
    await _assert_prepared_ref_empty(
        user_id=30,
        update_id=133,
        query=PREPARED_REF_PREFIX + "Z" * 64,
        prepared=FakePreparedResults(),
    )


@pytest.mark.unit
async def test_inline_quota_and_long_preview() -> None:
    catalog = FakeConsentCatalog()
    uow = InMemoryUnitOfWorkFactory()
    long_text = ("please stay calm " * 8).strip()
    generator = FakeTextGenerator(
        soften_result=SoftenResult(
            variants=(
                Variant(text="", firmness=Firmness.GENTLE),
                Variant(text=long_text, firmness=Firmness.BALANCED),
            ),
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
    await _onboard(bot, lifecycle, 31, catalog)
    await lifecycle.dispatcher.feed_update(bot, _inline(140, 31, "long enough draft"))
    await _await_inline(deps)
    answers = [req for req in session.requests if isinstance(req, AnswerInlineQuery)]
    article = answers[-1].results[0]
    assert isinstance(article, InlineQueryResultArticle)
    assert len(article.description or "") == 64
    quota_deps = make_telegram_deps(
        TelegramTestDeps(uow=uow, catalog=catalog, inline_quota_limit=0)
    )
    quota_session = FakeTelegramSession()
    quota_bot = Bot(token="1:TEST", session=quota_session)
    quota_life = build_telegram_lifecycle(_settings(), quota_deps, bot=quota_bot)
    await quota_life.dispatcher.feed_update(quota_bot, _inline(141, 31, "long enough draft"))
    await _await_inline(quota_deps)
    quota_answers = [req for req in quota_session.requests if isinstance(req, AnswerInlineQuery)]
    assert quota_answers[-1].results == []
    assert quota_answers[-1].button is not None
    assert quota_answers[-1].button.start_parameter == "help"
