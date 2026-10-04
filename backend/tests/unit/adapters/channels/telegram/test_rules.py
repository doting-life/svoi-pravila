"""Telegram /rules flow, archive confirm, and solo-rule ACTIVE list."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest
from aiogram import Bot
from aiogram.methods import EditMessageReplyMarkup, SendMessage
from aiogram.types import CallbackQuery, Chat, InaccessibleMessage, Message, Update, User
from tests.factories import make_settings
from tests.fakes.clock import FakeClock
from tests.fakes.consent_catalog import FakeConsentCatalog
from tests.fakes.dialog import FakeDialogState
from tests.fakes.generation import FakeTextGenerator
from tests.fakes.ids import FakeIdGenerator
from tests.fakes.rate_limit import FakePseudonymizer
from tests.fakes.telegram_deps import TelegramTestDeps, make_telegram_deps
from tests.fakes.telegram_session import FakeTelegramSession
from tests.fakes.uow import InMemoryUnitOfWorkFactory
from tests.fakes.usage_sink import RecordingUsageEventSink

from svoi_pravila.adapters.channels.telegram.factory import build_telegram_lifecycle
from svoi_pravila.adapters.channels.telegram.handlers.rules import (
    AwaitingRuleText,
    _parse_category,
    _parse_rule_id,
    dialog_text,
    rules_command,
)
from svoi_pravila.adapters.channels.telegram.keyboards import (
    archive_rule_confirm_keyboard,
    rule_category_keyboard,
    rules_keyboard,
)
from svoi_pravila.adapters.channels.telegram.lifecycle import TelegramLifecycle
from svoi_pravila.adapters.channels.telegram.localization import (
    load_ru_strings,
    rule_category_label,
)
from svoi_pravila.adapters.channels.telegram.presenters import (
    TELEGRAM_MESSAGE_MAX,
    display_rule_text,
    pack_message_lines,
    render_rules_list,
)
from svoi_pravila.application.errors import AccessNotGranted, NotFound
from svoi_pravila.application.ports.dialog_state import DIALOG_PSEUDONYM_PURPOSE, DialogRecord
from svoi_pravila.application.ports.generation import (
    DecodeResult,
    GenerationMeta,
    SafetyVerdict,
    TokenUsage,
    Variant,
)
from svoi_pravila.application.use_cases.archive_rule import (
    ArchiveRule,
    ArchiveRuleCommand,
    ArchiveRuleResult,
)
from svoi_pravila.application.use_cases.get_user_by_telegram_id import (
    GetUserByTelegramIdQuery,
    GetUserByTelegramIdResult,
)
from svoi_pravila.application.use_cases.list_rules import ListRulesCommand
from svoi_pravila.application.use_cases.propose_rule import (
    ProposeRule,
    ProposeRuleCommand,
    ProposeRuleResult,
)
from svoi_pravila.config import Environment, Settings, TelegramUpdatesMode
from svoi_pravila.domain.access import AccessStatus
from svoi_pravila.domain.enums import (
    ConsentKind,
    Firmness,
    RuleCategory,
    RuleStatus,
)
from svoi_pravila.domain.ids import ContactId, PairId, RuleId, TelegramUserId, UserId
from svoi_pravila.domain.rules import MAX_OPEN_RULES_PER_SCOPE, ContactScope, PairScope, Rule
from svoi_pravila.domain.text import RuleText

_NOW = datetime(2026, 10, 4, 12, tzinfo=UTC)
_RULE_SENTINEL = "SENTINEL_RULE_TEXT_0009"
_OWNER = UserId(UUID(int=1))
_PARTNER = UserId(UUID(int=2))


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


def _inaccessible(update_id: int, user_id: int, data: str) -> Update:
    return Update(
        update_id=update_id,
        callback_query=CallbackQuery(
            id=str(update_id),
            from_user=User(id=user_id, is_bot=False, first_name="A"),
            chat_instance="x",
            data=data,
            message=InaccessibleMessage(
                chat=Chat(id=user_id, type="private"), message_id=1, date=0
            ),
        ),
    )


async def _onboard(
    bot: Bot,
    lifecycle: TelegramLifecycle,
    user_id: int,
    catalog: FakeConsentCatalog,
) -> None:
    start = 10_000 + user_id * 10
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


def _dialog_key(user_id: int) -> str:
    return FakePseudonymizer().pseudonymize(DIALOG_PSEUDONYM_PURPOSE, str(user_id))


async def _add_contact(bot: Bot, lifecycle: TelegramLifecycle, user_id: int, label: str) -> None:
    base = 20_000 + user_id * 10
    await lifecycle.dispatcher.feed_update(bot, _callback(base, user_id, "ct:n"))
    await lifecycle.dispatcher.feed_update(bot, _callback(base + 1, user_id, "ct:rel:friend"))
    await lifecycle.dispatcher.feed_update(bot, _text_update(base + 2, user_id, label))


@pytest.mark.unit
async def test_rules_add_solo_becomes_active_and_archive() -> None:
    uow = InMemoryUnitOfWorkFactory()
    catalog = FakeConsentCatalog()
    clock = FakeClock(start=datetime(2026, 10, 3, 12, tzinfo=UTC))
    dialog = FakeDialogState()
    sink = RecordingUsageEventSink()
    generator = FakeTextGenerator(
        decode_result=DecodeResult(
            hypotheses=("h",),
            underlying_request="u",
            variants=(
                Variant(text="g", firmness=Firmness.GENTLE),
                Variant(text="b", firmness=Firmness.BALANCED),
                Variant(text="f", firmness=Firmness.FIRM),
            ),
            applied_rule_indexes=(0,),
            safety=SafetyVerdict.OK,
            meta=GenerationMeta(
                model="fake",
                prompt_version="decode@v1",
                latency_ms=1,
                attempts=1,
                usage=TokenUsage(),
            ),
        )
    )
    deps = make_telegram_deps(
        TelegramTestDeps(
            uow=uow,
            catalog=catalog,
            clock=clock,
            dialog=dialog,
            generator=generator,
            sink=sink,
        )
    )
    session = FakeTelegramSession()
    bot = Bot(token="1:TEST", session=session)
    lifecycle = build_telegram_lifecycle(_settings(), deps, bot=bot)
    await _onboard(bot, lifecycle, 601, catalog)
    await lifecycle.dispatcher.feed_update(bot, _text_update(1, 601, "/rules"))
    assert deps.strings.rules_no_active_contact in _sent_texts(session)
    await _add_contact(bot, lifecycle, 601, "Sam")
    await lifecycle.dispatcher.feed_update(bot, _text_update(40, 601, "/rules"))
    assert any(deps.strings.rules_empty in text for text in _sent_texts(session))
    await lifecycle.dispatcher.feed_update(bot, _callback(3, 601, "ru:n"))
    assert any(isinstance(req, EditMessageReplyMarkup) for req in session.requests)
    await lifecycle.dispatcher.feed_update(bot, _callback(4, 601, "ru:cat:other"))
    stored = await dialog.get(_dialog_key(601))
    assert stored is not None
    assert stored.step == "awaiting_rule_text"
    assert stored.category is RuleCategory.OTHER
    await lifecycle.dispatcher.feed_update(bot, _text_update(5, 601, ""))
    assert deps.strings.rules_invalid_text in _sent_texts(session)
    assert await dialog.get(_dialog_key(601)) is not None
    await lifecycle.dispatcher.feed_update(bot, _text_update(6, 601, "не повышать голос"))
    assert await dialog.get(_dialog_key(601)) is None
    listed = _sent_texts(session)[-1]
    assert "не повышать голос" in listed
    assert "3 октября" in listed
    assert deps.strings.rules_proposed_mark not in listed
    owner = (
        await deps.get_user_by_telegram_id.execute(GetUserByTelegramIdQuery(TelegramUserId(601)))
    ).user
    assert owner is not None
    assert owner.active_contact_id is not None
    rules = (
        await deps.list_rules.execute(ListRulesCommand(owner.id, owner.active_contact_id))
    ).rules
    assert len(rules) == 1
    assert rules[0].status is RuleStatus.ACTIVE
    clock.advance(timedelta(days=1))
    await lifecycle.dispatcher.feed_update(bot, _text_update(7, 601, "please decode this now"))
    assert generator.decode_stream_calls
    citations = [text for text in _sent_texts(session) if "Учтено правило от" in text]
    assert citations[-1] == "Учтено правило от 3 октября: «не повышать голос»"


@pytest.mark.unit
async def test_rules_archive_confirm_and_cancel() -> None:
    uow = InMemoryUnitOfWorkFactory()
    catalog = FakeConsentCatalog()
    deps = make_telegram_deps(TelegramTestDeps(uow=uow, catalog=catalog))
    session = FakeTelegramSession()
    bot = Bot(token="1:TEST", session=session)
    lifecycle = build_telegram_lifecycle(_settings(), deps, bot=bot)
    await _onboard(bot, lifecycle, 608, catalog)
    await _add_contact(bot, lifecycle, 608, "Sam")
    await lifecycle.dispatcher.feed_update(bot, _callback(1, 608, "ru:n"))
    await lifecycle.dispatcher.feed_update(bot, _callback(2, 608, "ru:cat:other"))
    await lifecycle.dispatcher.feed_update(bot, _text_update(3, 608, "не повышать голос"))
    owner = (
        await deps.get_user_by_telegram_id.execute(GetUserByTelegramIdQuery(TelegramUserId(608)))
    ).user
    assert owner is not None
    assert owner.active_contact_id is not None
    rules = (
        await deps.list_rules.execute(ListRulesCommand(owner.id, owner.active_contact_id))
    ).rules
    rule_id = rules[0].id
    await lifecycle.dispatcher.feed_update(bot, _callback(4, 608, f"ru:ar:{rule_id}"))
    assert deps.strings.rules_archive_confirm.format(text="не повышать голос") in _sent_texts(
        session
    )
    await lifecycle.dispatcher.feed_update(bot, _callback(5, 608, "ru:ax"))
    assert "не повышать голос" in _sent_texts(session)[-1]
    await lifecycle.dispatcher.feed_update(bot, _callback(6, 608, f"ru:ar:{rule_id}"))
    await lifecycle.dispatcher.feed_update(bot, _callback(7, 608, f"ru:ay:{rule_id}"))
    assert deps.strings.rules_empty in _sent_texts(session)[-1]


@pytest.mark.unit
async def test_rule_text_sentinel_absent_from_logs(
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
    bot = Bot(token="1:TEST", session=session)
    lifecycle = build_telegram_lifecycle(_settings(), deps, bot=bot)
    await _onboard(bot, lifecycle, 602, catalog)
    await _add_contact(bot, lifecycle, 602, "Sam")
    await lifecycle.dispatcher.feed_update(bot, _callback(1, 602, "ru:n"))
    await lifecycle.dispatcher.feed_update(bot, _callback(2, 602, "ru:cat:taboo_topic"))
    await lifecycle.dispatcher.feed_update(bot, _text_update(3, 602, _RULE_SENTINEL))
    blob = " ".join(str(event) for event in capture_log_events())
    assert _RULE_SENTINEL not in blob
    assert generator.decode_stream_calls == []
    assert sink.events == []
    assert _RULE_SENTINEL in _sent_texts(session)[-1]


@pytest.mark.unit
async def test_rules_stranger_cannot_archive_or_add() -> None:
    uow = InMemoryUnitOfWorkFactory()
    catalog = FakeConsentCatalog()
    deps = make_telegram_deps(TelegramTestDeps(uow=uow, catalog=catalog))
    session = FakeTelegramSession()
    bot = Bot(token="1:TEST", session=session)
    lifecycle = build_telegram_lifecycle(_settings(), deps, bot=bot)
    await lifecycle.dispatcher.feed_update(bot, _text_update(1, 603, "/rules"))
    assert deps.strings.age_prompt in _sent_texts(session)
    await lifecycle.dispatcher.feed_update(bot, _callback(2, 603, "ru:n"))
    await _onboard(bot, lifecycle, 604, catalog)
    await _onboard(bot, lifecycle, 605, catalog)
    await _add_contact(bot, lifecycle, 604, "Mine")
    await lifecycle.dispatcher.feed_update(bot, _callback(3, 604, "ru:n"))
    await lifecycle.dispatcher.feed_update(bot, _callback(4, 604, "ru:cat:apology"))
    await lifecycle.dispatcher.feed_update(bot, _text_update(5, 604, "не шутить"))
    owner = (
        await deps.get_user_by_telegram_id.execute(GetUserByTelegramIdQuery(TelegramUserId(604)))
    ).user
    assert owner is not None
    assert owner.active_contact_id is not None
    rules = (
        await deps.list_rules.execute(ListRulesCommand(owner.id, owner.active_contact_id))
    ).rules
    foreign = rules[0].id
    before = len(_sent_texts(session))
    await lifecycle.dispatcher.feed_update(bot, _callback(6, 605, f"ru:ay:{foreign}"))
    after = _sent_texts(session)[before:]
    assert any(deps.strings.error_generic in text for text in after)
    assert "не шутить" not in "".join(after)


@pytest.mark.unit
async def test_rules_edges_parse_inaccessible_and_limit() -> None:
    assert _parse_category(None) is None
    assert _parse_category("ru:cat:nope") is None
    assert _parse_category("x:cat:other") is None
    assert _parse_rule_id(None, "ar") is None
    assert _parse_rule_id("ru:ar:nope", "ar") is None
    assert _parse_rule_id(f"ru:ax:{UUID(int=1)}", "ay") is None
    uow = InMemoryUnitOfWorkFactory()
    catalog = FakeConsentCatalog()
    ids = FakeIdGenerator()
    dialog = FakeDialogState()
    deps = make_telegram_deps(TelegramTestDeps(uow=uow, catalog=catalog, dialog=dialog, ids=ids))
    session = FakeTelegramSession()
    bot = Bot(token="1:TEST", session=session)
    lifecycle = build_telegram_lifecycle(_settings(), deps, bot=bot)
    await _onboard(bot, lifecycle, 606, catalog)
    await lifecycle.dispatcher.feed_update(bot, _callback(1, 606, "ru:n"))
    assert deps.strings.rules_no_active_contact in _sent_texts(session)
    await lifecycle.dispatcher.feed_update(bot, _callback(20, 606, f"ru:ar:{UUID(int=1)}"))
    await lifecycle.dispatcher.feed_update(bot, _callback(2, 606, "ru:cat:other"))
    assert deps.strings.rules_no_active_contact in _sent_texts(session)
    await _add_contact(bot, lifecycle, 606, "Sam")
    await lifecycle.dispatcher.feed_update(bot, _callback(3, 606, "ru:cat:nope"))
    await lifecycle.dispatcher.feed_update(bot, _inaccessible(4, 606, "ru:n"))
    await lifecycle.dispatcher.feed_update(bot, _inaccessible(5, 606, "ru:cat:other"))
    await lifecycle.dispatcher.feed_update(bot, _inaccessible(6, 606, f"ru:ar:{UUID(int=1)}"))
    await lifecycle.dispatcher.feed_update(bot, _inaccessible(7, 606, f"ru:ay:{UUID(int=1)}"))
    await lifecycle.dispatcher.feed_update(bot, _inaccessible(8, 606, "ru:ax"))
    await lifecycle.dispatcher.feed_update(bot, _callback(9, 606, "ru:ar:nope"))
    await lifecycle.dispatcher.feed_update(bot, _callback(10, 606, "ru:ay:nope"))
    await dialog.set(_dialog_key(606), DialogRecord(step="awaiting_rule_text"))
    await lifecycle.dispatcher.feed_update(bot, _text_update(11, 606, "body"))
    assert deps.strings.error_generic in _sent_texts(session)
    user = (
        await deps.get_user_by_telegram_id.execute(GetUserByTelegramIdQuery(TelegramUserId(606)))
    ).user
    assert user is not None
    assert user.active_contact_id is not None
    propose = ProposeRule(uow, catalog, ids, deps.clock)

    for i in range(MAX_OPEN_RULES_PER_SCOPE):
        await propose.execute(
            ProposeRuleCommand(
                user.id,
                user.active_contact_id,
                RuleCategory.OTHER,
                RuleText(f"open rule {i}"),
                shared=False,
            )
        )
    await dialog.set(
        _dialog_key(606),
        DialogRecord(
            step="awaiting_rule_text",
            contact_id=user.active_contact_id,
            category=RuleCategory.OTHER,
        ),
    )
    await lifecycle.dispatcher.feed_update(bot, _text_update(12, 606, "overflow rule"))
    assert deps.strings.rules_limit in _sent_texts(session)
    assert await dialog.get(_dialog_key(606)) is None


@pytest.mark.unit
async def test_rules_handlers_skip_missing_user_and_access() -> None:
    deps = make_telegram_deps()
    bare = Message(
        message_id=1,
        date=_NOW,
        chat=Chat(id=1, type="private"),
        text="/rules",
    )
    await rules_command(bare, deps)
    assert await AwaitingRuleText()(bare, deps) is False
    await dialog_text(bare, deps)
    command_only = Message(
        message_id=2,
        date=_NOW,
        chat=Chat(id=607, type="private"),
        from_user=User(id=607, is_bot=False, first_name="A"),
        text="/help",
    )
    assert await AwaitingRuleText()(command_only, deps) is False
    uow = InMemoryUnitOfWorkFactory()
    catalog = FakeConsentCatalog()
    dialog = FakeDialogState()
    deps = make_telegram_deps(TelegramTestDeps(uow=uow, catalog=catalog, dialog=dialog))
    session = FakeTelegramSession()
    bot = Bot(token="1:TEST", session=session)
    lifecycle = build_telegram_lifecycle(_settings(), deps, bot=bot)
    await _onboard(bot, lifecycle, 607, catalog)
    await _add_contact(bot, lifecycle, 607, "Sam")
    hidden = replace(
        deps, get_user_by_telegram_id=cast(Any, _HideUser(deps.get_user_by_telegram_id))
    )
    lifecycle = build_telegram_lifecycle(_settings(), hidden, bot=bot)
    await lifecycle.dispatcher.feed_update(bot, _text_update(1, 607, "/rules"))
    await lifecycle.dispatcher.feed_update(bot, _callback(2, 607, "ru:n"))
    await lifecycle.dispatcher.feed_update(bot, _callback(3, 607, "ru:cat:other"))
    await lifecycle.dispatcher.feed_update(bot, _callback(8, 607, f"ru:ar:{UUID(int=1)}"))
    await lifecycle.dispatcher.feed_update(bot, _callback(4, 607, f"ru:ay:{UUID(int=1)}"))
    await lifecycle.dispatcher.feed_update(bot, _callback(5, 607, "ru:ax"))
    denied = replace(deps, propose_rule=cast(ProposeRule, _AccessDeniedPropose()))
    denied = replace(denied, archive_rule=cast(ArchiveRule, _AccessDeniedArchive()))
    lifecycle = build_telegram_lifecycle(_settings(), denied, bot=bot)
    await dialog.set(
        _dialog_key(607),
        DialogRecord(
            step="awaiting_rule_text",
            contact_id=ContactId(UUID(int=1)),
            category=RuleCategory.OTHER,
        ),
    )
    await lifecycle.dispatcher.feed_update(bot, _text_update(6, 607, "denied body"))
    await lifecycle.dispatcher.feed_update(bot, _callback(7, 607, f"ru:ay:{UUID(int=1)}"))


@pytest.mark.unit
async def test_rules_onboarding_and_missing_dialog_actor() -> None:
    uow = InMemoryUnitOfWorkFactory()
    catalog = FakeConsentCatalog()
    dialog = FakeDialogState()
    deps = make_telegram_deps(TelegramTestDeps(uow=uow, catalog=catalog, dialog=dialog))
    session = FakeTelegramSession()
    bot = Bot(token="1:TEST", session=session)
    lifecycle = build_telegram_lifecycle(_settings(), deps, bot=bot)
    await lifecycle.dispatcher.feed_update(bot, _callback(1, 609, "ru:cat:other"))
    await lifecycle.dispatcher.feed_update(bot, _callback(2, 609, f"ru:ar:{UUID(int=1)}"))
    await lifecycle.dispatcher.feed_update(bot, _callback(3, 609, f"ru:ay:{UUID(int=1)}"))
    await lifecycle.dispatcher.feed_update(bot, _callback(4, 609, "ru:ax"))
    assert deps.strings.age_prompt in _sent_texts(session)
    message = Message(
        message_id=1,
        date=_NOW,
        chat=Chat(id=609, type="private"),
        from_user=User(id=609, is_bot=False, first_name="A"),
        text="body",
    )
    await dialog_text(message, deps)
    await dialog.set(
        _dialog_key(609),
        DialogRecord(step="awaiting_rule_text", contact_id=ContactId(UUID(int=1))),
    )
    await lifecycle.dispatcher.feed_update(bot, _text_update(5, 609, "still onboarding"))
    await _onboard(bot, lifecycle, 610, catalog)
    await _onboard(bot, lifecycle, 611, catalog)
    await dialog.set(
        _dialog_key(610),
        DialogRecord(
            step="awaiting_rule_text",
            contact_id=ContactId(UUID(int=1)),
            category=RuleCategory.OTHER,
        ),
    )
    hidden = replace(
        deps, get_user_by_telegram_id=cast(Any, _HideUser(deps.get_user_by_telegram_id))
    )
    hidden_life = build_telegram_lifecycle(_settings(), hidden, bot=bot)
    await hidden_life.dispatcher.feed_update(bot, _text_update(6, 610, "hidden actor"))
    await lifecycle.dispatcher.feed_update(bot, _callback(8, 611, "ru:ax"))
    assert any(deps.strings.rules_no_active_contact in text for text in _sent_texts(session))


@pytest.mark.unit
def test_rules_keyboards_and_presenter_hides_closed() -> None:
    strings = load_ru_strings()
    ident = RuleId(UUID(int=1))
    keyboard = rules_keyboard(strings, (ident,))
    payloads = [btn.callback_data for row in keyboard.inline_keyboard for btn in row]
    assert "ru:n" in payloads
    assert f"ru:ar:{ident}" in payloads
    confirm = archive_rule_confirm_keyboard(strings, ident)
    confirm_payloads = [btn.callback_data for row in confirm.inline_keyboard for btn in row]
    assert f"ru:ay:{ident}" in confirm_payloads
    assert "ru:ax" in confirm_payloads
    cats = rule_category_keyboard(strings)
    kinds = {kind.value for kind in RuleCategory}
    found = {
        btn.callback_data.split(":")[2]
        for row in cats.inline_keyboard
        for btn in row
        if btn.callback_data is not None
    }
    assert found == kinds
    for kind in RuleCategory:
        assert rule_category_label(strings, kind)
    now = datetime(2026, 10, 4, 12, tzinfo=UTC)
    tz = ZoneInfo("Europe/Moscow")
    active = Rule.propose(
        rule_id=RuleId(UUID(int=10)),
        scope=ContactScope(contact_id=ContactId(UUID(int=20))),
        category=RuleCategory.OTHER,
        approvers=frozenset({_OWNER}),
        author_id=_OWNER,
        text=RuleText("active text"),
        now=datetime(2026, 10, 3, 12, tzinfo=UTC),
    )
    proposed = Rule.propose(
        rule_id=RuleId(UUID(int=11)),
        scope=PairScope(pair_id=PairId(UUID(int=21))),
        category=RuleCategory.TABOO_TOPIC,
        approvers=frozenset({_OWNER, _PARTNER}),
        author_id=_OWNER,
        text=RuleText("pending text"),
        now=datetime(2026, 10, 3, 12, tzinfo=UTC),
    )
    archived = active.archive(_OWNER, now)
    rejected = proposed.reject_pending(_PARTNER, now)
    chunks, keyboard = render_rules_list(
        strings, (active, proposed, archived, rejected), now=now, tz=tz
    )
    text = "\n".join(chunks)
    joined = " ".join(btn.callback_data or "" for row in keyboard.inline_keyboard for btn in row)
    assert "1. active text — 3 октября" in text
    assert "2. pending text — " in text
    buttons = [btn.text for row in keyboard.inline_keyboard for btn in row]
    assert strings.rules_archive.format(n=1) in buttons
    assert strings.rules_archive.format(n=2) in buttons
    assert strings.rules_proposed_mark in text
    assert joined.count("ru:ar:") == 2
    assert f"ru:ar:{active.id}" in joined
    assert f"ru:ar:{proposed.id}" in joined
    assert archived.status is RuleStatus.ARCHIVED
    assert rejected.status is RuleStatus.REJECTED
    assert display_rule_text(active) == "active text"
    assert display_rule_text(proposed) == "pending text"
    assert display_rule_text(archived) == "active text"
    packed = pack_message_lines(("aa", "bb", "c"), max_len=5)
    assert packed == ("aa\nbb", "c")
    assert pack_message_lines(()) == ()


@pytest.mark.unit
def test_rules_list_splits_max_open_max_text() -> None:
    strings = load_ru_strings()
    now = datetime(2026, 10, 4, 12, tzinfo=UTC)
    tz = ZoneInfo("Europe/Moscow")
    body = "я" * 280
    rules = tuple(
        Rule.propose(
            rule_id=RuleId(UUID(int=2000 + index)),
            scope=ContactScope(contact_id=ContactId(UUID(int=20))),
            category=RuleCategory.OTHER,
            approvers=frozenset({_OWNER}),
            author_id=_OWNER,
            text=RuleText(body),
            now=datetime(2026, 10, 3, 12, tzinfo=UTC),
        )
        for index in range(MAX_OPEN_RULES_PER_SCOPE)
    )
    chunks, keyboard = render_rules_list(strings, rules, now=now, tz=tz)
    joined = "\n".join(chunks)
    assert chunks
    assert all(len(chunk) <= TELEGRAM_MESSAGE_MAX for chunk in chunks)
    assert joined.count(body) == MAX_OPEN_RULES_PER_SCOPE
    numbers = [
        int(line.split(".", 1)[0])
        for chunk in chunks
        for line in chunk.split("\n")
        if line[:1].isdigit()
    ]
    assert numbers == list(range(1, MAX_OPEN_RULES_PER_SCOPE + 1))
    buttons = [btn for row in keyboard.inline_keyboard for btn in row]
    assert len(buttons) == MAX_OPEN_RULES_PER_SCOPE + 1
    assert len(buttons) <= 100


@pytest.mark.unit
async def test_rule_limit_clears_dialog_then_decode() -> None:
    uow = InMemoryUnitOfWorkFactory()
    catalog = FakeConsentCatalog()
    ids = FakeIdGenerator()
    dialog = FakeDialogState()
    generator = FakeTextGenerator()
    deps = make_telegram_deps(
        TelegramTestDeps(uow=uow, catalog=catalog, dialog=dialog, ids=ids, generator=generator)
    )
    session = FakeTelegramSession()
    bot = Bot(token="1:TEST", session=session)
    lifecycle = build_telegram_lifecycle(_settings(), deps, bot=bot)
    await _onboard(bot, lifecycle, 620, catalog)
    await _add_contact(bot, lifecycle, 620, "Sam")
    user = (
        await deps.get_user_by_telegram_id.execute(GetUserByTelegramIdQuery(TelegramUserId(620)))
    ).user
    assert user is not None
    assert user.active_contact_id is not None
    propose = ProposeRule(uow, catalog, ids, deps.clock)
    body = "я" * 280
    for _i in range(MAX_OPEN_RULES_PER_SCOPE):
        await propose.execute(
            ProposeRuleCommand(
                user.id,
                user.active_contact_id,
                RuleCategory.OTHER,
                RuleText(body),
                shared=False,
            )
        )
    n_before = len(_sent_texts(session))
    await lifecycle.dispatcher.feed_update(bot, _text_update(20, 620, "/rules"))
    listed_msgs = _sent_texts(session)[n_before:]
    assert len(listed_msgs) > 1
    assert all(len(text) <= TELEGRAM_MESSAGE_MAX for text in listed_msgs)
    n_before = len(_sent_texts(session))
    await lifecycle.dispatcher.feed_update(bot, _callback(21, 620, "ru:ax"))
    listed_msgs = _sent_texts(session)[n_before:]
    assert len(listed_msgs) > 1
    assert all(len(text) <= TELEGRAM_MESSAGE_MAX for text in listed_msgs)
    await dialog.set(
        _dialog_key(620),
        DialogRecord(
            step="awaiting_rule_text",
            contact_id=user.active_contact_id,
            category=RuleCategory.OTHER,
        ),
    )
    await lifecycle.dispatcher.feed_update(bot, _text_update(1, 620, "overflow rule"))
    assert await dialog.get(_dialog_key(620)) is None
    await lifecycle.dispatcher.feed_update(bot, _text_update(2, 620, "please decode this now"))
    assert len(generator.decode_stream_calls) == 1


@pytest.mark.unit
async def test_rule_contact_gone_clears_dialog_then_decode() -> None:
    uow = InMemoryUnitOfWorkFactory()
    catalog = FakeConsentCatalog()
    dialog = FakeDialogState()
    generator = FakeTextGenerator()
    deps = make_telegram_deps(
        TelegramTestDeps(uow=uow, catalog=catalog, dialog=dialog, generator=generator)
    )
    deps = replace(deps, propose_rule=cast(ProposeRule, _AccessDeniedPropose()))
    session = FakeTelegramSession()
    bot = Bot(token="1:TEST", session=session)
    lifecycle = build_telegram_lifecycle(_settings(), deps, bot=bot)
    await _onboard(bot, lifecycle, 621, catalog)
    await _add_contact(bot, lifecycle, 621, "Sam")
    await dialog.set(
        _dialog_key(621),
        DialogRecord(
            step="awaiting_rule_text",
            contact_id=ContactId(UUID(int=1)),
            category=RuleCategory.OTHER,
        ),
    )
    await lifecycle.dispatcher.feed_update(bot, _text_update(1, 621, "gone contact body"))
    assert deps.strings.rules_contact_unavailable in _sent_texts(session)
    assert await dialog.get(_dialog_key(621)) is None
    await lifecycle.dispatcher.feed_update(bot, _text_update(2, 621, "please decode this now"))
    assert len(generator.decode_stream_calls) == 1


@pytest.mark.unit
async def test_invalid_rule_text_keeps_dialog() -> None:
    uow = InMemoryUnitOfWorkFactory()
    catalog = FakeConsentCatalog()
    dialog = FakeDialogState()
    generator = FakeTextGenerator()
    deps = make_telegram_deps(
        TelegramTestDeps(uow=uow, catalog=catalog, dialog=dialog, generator=generator)
    )
    session = FakeTelegramSession()
    bot = Bot(token="1:TEST", session=session)
    lifecycle = build_telegram_lifecycle(_settings(), deps, bot=bot)
    await _onboard(bot, lifecycle, 622, catalog)
    await _add_contact(bot, lifecycle, 622, "Sam")
    await lifecycle.dispatcher.feed_update(bot, _callback(1, 622, "ru:n"))
    await lifecycle.dispatcher.feed_update(bot, _callback(2, 622, "ru:cat:other"))
    await lifecycle.dispatcher.feed_update(bot, _text_update(3, 622, ""))
    assert deps.strings.rules_invalid_text in _sent_texts(session)
    assert await dialog.get(_dialog_key(622)) is not None
    await lifecycle.dispatcher.feed_update(bot, _text_update(4, 622, ""))
    assert await dialog.get(_dialog_key(622)) is not None
    assert generator.decode_stream_calls == []


@pytest.mark.unit
async def test_stale_archive_already_archived_refreshes_list() -> None:
    uow = InMemoryUnitOfWorkFactory()
    catalog = FakeConsentCatalog()
    deps = make_telegram_deps(TelegramTestDeps(uow=uow, catalog=catalog))
    session = FakeTelegramSession()
    bot = Bot(token="1:TEST", session=session)
    lifecycle = build_telegram_lifecycle(_settings(), deps, bot=bot)
    await _onboard(bot, lifecycle, 623, catalog)
    await _add_contact(bot, lifecycle, 623, "Sam")
    await lifecycle.dispatcher.feed_update(bot, _callback(1, 623, "ru:n"))
    await lifecycle.dispatcher.feed_update(bot, _callback(2, 623, "ru:cat:other"))
    await lifecycle.dispatcher.feed_update(bot, _text_update(3, 623, "не повышать голос"))
    owner = (
        await deps.get_user_by_telegram_id.execute(GetUserByTelegramIdQuery(TelegramUserId(623)))
    ).user
    assert owner is not None
    assert owner.active_contact_id is not None
    rule_id = (
        (await deps.list_rules.execute(ListRulesCommand(owner.id, owner.active_contact_id)))
        .rules[0]
        .id
    )
    await lifecycle.dispatcher.feed_update(bot, _callback(4, 623, f"ru:ay:{rule_id}"))
    await lifecycle.dispatcher.feed_update(bot, _callback(5, 623, f"ru:ay:{rule_id}"))
    texts = _sent_texts(session)
    assert deps.strings.rules_already_archived in texts
    assert deps.strings.rules_empty in texts[-1]


@pytest.mark.unit
async def test_ask_archive_missing_rule_is_generic() -> None:
    uow = InMemoryUnitOfWorkFactory()
    catalog = FakeConsentCatalog()
    deps = make_telegram_deps(TelegramTestDeps(uow=uow, catalog=catalog))
    session = FakeTelegramSession()
    bot = Bot(token="1:TEST", session=session)
    lifecycle = build_telegram_lifecycle(_settings(), deps, bot=bot)
    await _onboard(bot, lifecycle, 624, catalog)
    await _add_contact(bot, lifecycle, 624, "Sam")
    await lifecycle.dispatcher.feed_update(bot, _callback(1, 624, f"ru:ar:{UUID(int=99)}"))
    assert deps.strings.error_generic in _sent_texts(session)[-1]


@pytest.mark.unit
async def test_ask_archive_list_not_found() -> None:
    uow = InMemoryUnitOfWorkFactory()
    catalog = FakeConsentCatalog()
    deps = make_telegram_deps(TelegramTestDeps(uow=uow, catalog=catalog))
    deps = replace(deps, list_rules=cast(Any, _MissingList()))
    session = FakeTelegramSession()
    bot = Bot(token="1:TEST", session=session)
    lifecycle = build_telegram_lifecycle(_settings(), deps, bot=bot)
    await _onboard(bot, lifecycle, 625, catalog)
    await _add_contact(bot, lifecycle, 625, "Sam")
    await lifecycle.dispatcher.feed_update(bot, _callback(1, 625, f"ru:ar:{UUID(int=1)}"))
    assert deps.strings.error_generic in _sent_texts(session)[-1]


class _HideUser:
    def __init__(self, inner: object) -> None:
        self._inner = inner

    async def execute(self, query: GetUserByTelegramIdQuery) -> GetUserByTelegramIdResult:
        await cast(Any, self._inner).execute(query)
        return GetUserByTelegramIdResult(user=None)


class _AccessDeniedPropose:
    async def execute(self, command: ProposeRuleCommand) -> ProposeRuleResult:
        raise AccessNotGranted(
            AccessStatus(age_confirmed=True, missing_consents=frozenset(), granted=False)
        )


class _AccessDeniedArchive:
    async def execute(self, command: ArchiveRuleCommand) -> ArchiveRuleResult:
        raise AccessNotGranted(
            AccessStatus(age_confirmed=True, missing_consents=frozenset(), granted=False)
        )


class _MissingList:
    async def execute(self, command: ListRulesCommand) -> None:
        raise NotFound()
