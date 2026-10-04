"""Telegram decode handler: streaming drafts, copy buttons, mapped errors."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime

import pytest
from aiogram import Bot
from aiogram.methods import SendMessage, SendMessageDraft
from aiogram.types import (
    CallbackQuery,
    Chat,
    Message,
    MessageOriginUser,
    PhotoSize,
    Update,
    User,
)
from tests.factories import make_settings
from tests.fakes.concurrency import FakeConcurrencyGuard
from tests.fakes.consent_catalog import FakeConsentCatalog
from tests.fakes.generation import FakeTextGenerator
from tests.fakes.telegram_deps import TelegramTestDeps, make_telegram_deps
from tests.fakes.telegram_session import FakeTelegramSession
from tests.fakes.uow import InMemoryUnitOfWorkFactory
from tests.fakes.usage_sink import RecordingUsageEventSink

from svoi_pravila.adapters.channels.telegram.factory import build_telegram_lifecycle
from svoi_pravila.adapters.channels.telegram.handlers.decode import (
    _decode_error_reply,
    _stream_decode,
)
from svoi_pravila.adapters.channels.telegram.keyboards import copy_text_markup
from svoi_pravila.adapters.channels.telegram.lifecycle import TelegramLifecycle
from svoi_pravila.adapters.channels.telegram.localization import load_ru_strings
from svoi_pravila.adapters.channels.telegram.presenters import (
    TELEGRAM_MESSAGE_MAX,
    render_decode_completed,
)
from svoi_pravila.application.errors import (
    AccessNotGranted,
    ConflictError,
    GenerationRefusedByProvider,
    GenerationUnavailable,
    InvalidGenerationOutput,
    InvalidOutputReason,
    NotFound,
    UnavailableKind,
)
from svoi_pravila.application.ports.generation import (
    DecodeCompleted,
    DecodeEvent,
    DecodeResult,
    Firmness,
    GenerationMeta,
    SafetyVerdict,
    TokenUsage,
    Variant,
)
from svoi_pravila.application.use_cases.decode_incoming import DecodeIncomingCommand
from svoi_pravila.config import Environment, Settings, TelegramUpdatesMode
from svoi_pravila.domain.access import AccessStatus
from svoi_pravila.domain.enums import ConsentKind

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


async def _onboard(
    bot: Bot,
    lifecycle: TelegramLifecycle,
    user_id: int,
    catalog: FakeConsentCatalog,
) -> None:
    start = 1000 + user_id
    await lifecycle.dispatcher.feed_update(bot, _text_update(start, user_id, "/start"))
    await lifecycle.dispatcher.feed_update(bot, _callback(start + 1, user_id, "age:y"))
    pd = catalog.current_requirement().for_kind(ConsentKind.PERSONAL_DATA).version
    sc = catalog.current_requirement().for_kind(ConsentKind.SPECIAL_CATEGORY).version
    await lifecycle.dispatcher.feed_update(
        bot, _callback(start + 2, user_id, f"cg:personal_data:{pd}:y")
    )
    await lifecycle.dispatcher.feed_update(
        bot, _callback(start + 3, user_id, f"cg:special_category:{sc}:y")
    )


def _sent_texts(session: FakeTelegramSession) -> list[str]:
    return [str(req.text) for req in session.requests if isinstance(req, SendMessage)]


@pytest.mark.unit
async def test_decode_streams_and_copy_buttons() -> None:
    uow = InMemoryUnitOfWorkFactory()
    catalog = FakeConsentCatalog()
    sink = RecordingUsageEventSink()
    generator = FakeTextGenerator(stream_chunks=("part-a", "part-b"))
    deps = make_telegram_deps(
        TelegramTestDeps(
            uow=uow,
            catalog=catalog,
            generator=generator,
            sink=sink,
            draft_min_interval_ms=50,
        )
    )
    session = FakeTelegramSession()
    bot = Bot(token="1:TEST", session=session)
    lifecycle = build_telegram_lifecycle(_settings(), deps, bot=bot)
    await _onboard(bot, lifecycle, 401, catalog)
    await lifecycle.dispatcher.feed_update(bot, _text_update(10, 401, "please decode this"))
    assert any(isinstance(req, SendMessageDraft) for req in session.requests)
    finals = [req for req in session.requests if isinstance(req, SendMessage)]
    assert any(req.reply_markup is not None for req in finals)
    assert all(len(str(req.text)) <= TELEGRAM_MESSAGE_MAX for req in finals)
    assert sink.events


@pytest.mark.unit
async def test_decode_ignores_captions_and_asks_for_text() -> None:
    uow = InMemoryUnitOfWorkFactory()
    catalog = FakeConsentCatalog()
    generator = FakeTextGenerator()
    deps = make_telegram_deps(TelegramTestDeps(uow=uow, catalog=catalog, generator=generator))
    session = FakeTelegramSession()
    bot = Bot(token="1:TEST", session=session)
    lifecycle = build_telegram_lifecycle(_settings(), deps, bot=bot)
    await _onboard(bot, lifecycle, 402, catalog)
    await lifecycle.dispatcher.feed_update(
        bot,
        Update(
            update_id=20,
            message=Message(
                message_id=2,
                date=_NOW,
                chat=Chat(id=402, type="private"),
                from_user=User(id=402, is_bot=False, first_name="A"),
                photo=[PhotoSize(file_id="f", file_unique_id="u", width=1, height=1)],
            ),
        ),
    )
    assert deps.strings.decode_need_text in _sent_texts(session)
    await lifecycle.dispatcher.feed_update(
        bot,
        Update(
            update_id=21,
            message=Message(
                message_id=3,
                date=_NOW,
                chat=Chat(id=402, type="private"),
                from_user=User(id=402, is_bot=False, first_name="A"),
                caption="from caption",
                photo=[PhotoSize(file_id="f2", file_unique_id="u2", width=1, height=1)],
            ),
        ),
    )
    assert generator.decode_stream_calls == []
    assert _sent_texts(session).count(deps.strings.decode_need_text) == 2


@pytest.mark.unit
async def test_decode_forwarded_text_drops_origin() -> None:
    uow = InMemoryUnitOfWorkFactory()
    catalog = FakeConsentCatalog()
    generator = FakeTextGenerator()
    deps = make_telegram_deps(TelegramTestDeps(uow=uow, catalog=catalog, generator=generator))
    session = FakeTelegramSession()
    bot = Bot(token="1:TEST", session=session)
    lifecycle = build_telegram_lifecycle(_settings(), deps, bot=bot)
    await _onboard(bot, lifecycle, 405, catalog)
    origin_user = User(
        id=999000111,
        is_bot=False,
        first_name="ForwardedFirst",
        last_name="ForwardedLast",
        username="fwd_user",
    )
    await lifecycle.dispatcher.feed_update(
        bot,
        Update(
            update_id=22,
            message=Message(
                message_id=4,
                date=_NOW,
                chat=Chat(id=405, type="private"),
                from_user=User(id=405, is_bot=False, first_name="A"),
                text="only this body",
                forward_origin=MessageOriginUser(date=_NOW, sender_user=origin_user),
            ),
        ),
    )
    assert generator.decode_stream_calls[0].incoming == "only this body"


@pytest.mark.unit
async def test_decode_maps_errors() -> None:
    uow = InMemoryUnitOfWorkFactory()
    catalog = FakeConsentCatalog()

    class Busy(FakeConcurrencyGuard):
        async def acquire(self, key: str, *, ttl_seconds: int) -> str | None:
            self.acquire_calls.append((key, ttl_seconds))
            return None

    session = FakeTelegramSession()
    bot = Bot(token="1:TEST", session=session)
    busy_deps = make_telegram_deps(TelegramTestDeps(uow=uow, catalog=catalog, guard=Busy()))
    lifecycle = build_telegram_lifecycle(_settings(), busy_deps, bot=bot)
    await _onboard(bot, lifecycle, 403, catalog)
    await lifecycle.dispatcher.feed_update(bot, _text_update(30, 403, "x"))
    assert busy_deps.strings.decode_busy in _sent_texts(session)

    quota_deps = make_telegram_deps(
        TelegramTestDeps(uow=uow, catalog=catalog, quota_limit=0, rate_limit=1000)
    )
    session2 = FakeTelegramSession()
    bot2 = Bot(token="1:TEST", session=session2)
    life2 = build_telegram_lifecycle(_settings(), quota_deps, bot=bot2)
    await life2.dispatcher.feed_update(bot2, _text_update(31, 403, "x"))
    assert quota_deps.strings.decode_quota in _sent_texts(session2)

    cases = (
        (
            FakeTextGenerator(
                stream_error=InvalidGenerationOutput(
                    (InvalidOutputReason.JSON_DECODE,), usage=TokenUsage(), attempts=1
                )
            ),
            "decode_invalid",
        ),
        (
            FakeTextGenerator(
                stream_error=GenerationRefusedByProvider(usage=TokenUsage(), attempts=1)
            ),
            "decode_refused",
        ),
        (
            FakeTextGenerator(
                stream_error=GenerationUnavailable(
                    UnavailableKind.SERVER, usage=TokenUsage(), attempts=1
                )
            ),
            "decode_unavailable",
        ),
    )
    update_id = 32
    for generator, attr in cases:
        deps = make_telegram_deps(TelegramTestDeps(uow=uow, catalog=catalog, generator=generator))
        sess = FakeTelegramSession()
        b = Bot(token="1:TEST", session=sess)
        life = build_telegram_lifecycle(_settings(), deps, bot=b)
        await life.dispatcher.feed_update(b, _text_update(update_id, 403, "x"))
        update_id += 1
        assert getattr(deps.strings, attr) in _sent_texts(sess)


@pytest.mark.unit
def test_render_decode_safety_and_copy_truncation() -> None:
    strings = load_ru_strings()
    meta = GenerationMeta(
        model="m",
        prompt_version="p",
        latency_ms=1,
        attempts=1,
        usage=TokenUsage(),
    )
    crisis = DecodeCompleted(
        analysis="secret-analysis",
        result=DecodeResult(
            hypotheses=("h",),
            underlying_request="u",
            variants=(Variant(text="v", firmness=Firmness.FIRM),),
            applied_rule_indexes=(),
            safety=SafetyVerdict.CRISIS,
            meta=meta,
        ),
    )
    rendered = render_decode_completed(strings, crisis, copy_max=256)
    assert rendered == ((strings.decode_crisis, None),)
    refuse = DecodeCompleted(
        analysis="a",
        result=DecodeResult(
            hypotheses=(),
            underlying_request="",
            variants=(),
            applied_rule_indexes=(),
            safety=SafetyVerdict.REFUSE_MANIPULATION,
            meta=meta,
        ),
    )
    rendered = render_decode_completed(strings, refuse, copy_max=256)
    assert rendered == ((strings.decode_refuse_manipulation, None),)
    ok = DecodeCompleted(
        analysis="a",
        result=DecodeResult(
            hypotheses=("h",),
            underlying_request="u",
            variants=(Variant(text="z" * 300, firmness=Firmness.GENTLE),),
            applied_rule_indexes=(),
            safety=SafetyVerdict.OK,
            meta=meta,
        ),
    )
    rendered = render_decode_completed(strings, ok, copy_max=256)
    assert all(len(text) <= TELEGRAM_MESSAGE_MAX for text, _keyboard in rendered)
    long_keyboard = rendered[-1][1]
    assert long_keyboard is None
    short_ok = DecodeCompleted(
        analysis="a",
        result=DecodeResult(
            hypotheses=("h",),
            underlying_request="u",
            variants=(Variant(text="short-copy", firmness=Firmness.GENTLE),),
            applied_rule_indexes=(),
            safety=SafetyVerdict.OK,
            meta=meta,
        ),
    )
    short_rendered = render_decode_completed(strings, short_ok, copy_max=256)
    assert short_rendered[0] == ("a", None)
    assert short_rendered[1] == ("h\n\nu", None)
    short_keyboard = short_rendered[2][1]
    assert short_keyboard is not None
    button = short_keyboard.inline_keyboard[0][0]
    assert button.copy_text is not None
    assert button.copy_text.text == "short-copy"
    assert copy_text_markup(strings, "z" * 256, copy_max=256) is not None
    assert copy_text_markup(strings, "z" * 257, copy_max=256) is None
    assert copy_text_markup(strings, "", copy_max=256) is None


@pytest.mark.unit
def test_decode_error_reply_unknown_application_error() -> None:
    assert _decode_error_reply(ConflictError(), load_ru_strings()) is None


class _RaisingDecode:
    def __init__(self, exc: BaseException) -> None:
        self._exc = exc

    def execute(self, command: DecodeIncomingCommand) -> AsyncIterator[DecodeEvent]:
        del command
        return _RaisingStream(self._exc)


class _RaisingStream:
    def __init__(self, exc: BaseException) -> None:
        self._exc = exc

    def __aiter__(self) -> _RaisingStream:
        return self

    async def __anext__(self) -> DecodeEvent:
        raise self._exc


@pytest.mark.unit
async def test_decode_handler_edges() -> None:
    uow = InMemoryUnitOfWorkFactory()
    catalog = FakeConsentCatalog()
    strings = load_ru_strings()
    session = FakeTelegramSession()
    bot = Bot(token="1:TEST", session=session)
    deps = make_telegram_deps(TelegramTestDeps(uow=uow, catalog=catalog))
    lifecycle = build_telegram_lifecycle(_settings(), deps, bot=bot)
    await lifecycle.dispatcher.feed_update(
        bot,
        Update(
            update_id=1,
            message=Message(
                message_id=1,
                date=_NOW,
                chat=Chat(id=1, type="private"),
                text="no from",
            ),
        ),
    )
    await _onboard(bot, lifecycle, 404, catalog)
    before = len(_sent_texts(session))
    await lifecycle.dispatcher.feed_update(bot, _text_update(40, 404, "/unknown"))
    assert len(_sent_texts(session)) == before

    not_found_deps = replace(deps, decode_incoming=_RaisingDecode(NotFound()))
    life_nf = build_telegram_lifecycle(
        _settings(), not_found_deps, bot=Bot(token="1:TEST", session=FakeTelegramSession())
    )
    bot_nf = life_nf.bot
    await life_nf.dispatcher.feed_update(bot_nf, _text_update(41, 404, "hello"))

    conflict_session = FakeTelegramSession()
    conflict_bot = Bot(token="1:TEST", session=conflict_session)
    conflict_deps = replace(deps, decode_incoming=_RaisingDecode(ConflictError()))
    life_c = build_telegram_lifecycle(_settings(), conflict_deps, bot=conflict_bot)
    await life_c.dispatcher.feed_update(conflict_bot, _text_update(42, 404, "hello"))
    assert strings.error_generic in _sent_texts(conflict_session)

    await _stream_decode(
        Message(
            message_id=9,
            date=_NOW,
            chat=Chat(id=9, type="private"),
            text="x",
        ),
        deps,
        bot,
        "x",
    )
    assert copy_text_markup(strings, "", copy_max=256) is None
    access_deps = replace(
        deps,
        decode_incoming=_RaisingDecode(
            AccessNotGranted(
                AccessStatus(
                    age_confirmed=True,
                    missing_consents=frozenset(),
                    granted=False,
                )
            )
        ),
    )
    life_a = build_telegram_lifecycle(
        _settings(), access_deps, bot=Bot(token="1:TEST", session=FakeTelegramSession())
    )
    await life_a.dispatcher.feed_update(life_a.bot, _text_update(43, 404, "hello"))
