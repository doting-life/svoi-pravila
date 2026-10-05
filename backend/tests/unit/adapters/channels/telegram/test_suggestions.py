"""Tone suggestion DM, /rules pending block, sg: callbacks, feature-prefix registry."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any, cast
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.methods import EditMessageReplyMarkup, SendMessage
from aiogram.types import (
    CallbackQuery,
    Chat,
    ChosenInlineResult,
    Message,
    Update,
    User,
)
from tests.factories import make_settings
from tests.fakes.consent_catalog import FakeConsentCatalog
from tests.fakes.ids import FakeIdGenerator
from tests.fakes.pair_notifier import FakePairNotifier
from tests.fakes.telegram_deps import TelegramTestDeps, make_telegram_deps
from tests.fakes.telegram_session import FakeTelegramSession
from tests.fakes.uow import InMemoryUnitOfWorkFactory

from svoi_pravila.adapters.channels.telegram.factory import build_telegram_lifecycle
from svoi_pravila.adapters.channels.telegram.handlers.helpers import (
    FEATURE_CALLBACK_PREFIXES,
    is_feature_callback,
)
from svoi_pravila.adapters.channels.telegram.handlers.inline import _send_tone_suggestion_dm
from svoi_pravila.adapters.channels.telegram.handlers.rules import (
    _parse_suggestion_id,
    _pending_suggestions,
)
from svoi_pravila.adapters.channels.telegram.keyboards import suggestion_decision_keyboard
from svoi_pravila.adapters.channels.telegram.lifecycle import TelegramLifecycle
from svoi_pravila.adapters.channels.telegram.localization import load_ru_strings
from svoi_pravila.adapters.channels.telegram.presenters import render_suggestion_dm
from svoi_pravila.application.errors import AccessNotGranted, NotFound
from svoi_pravila.application.inline_result_ref import encode_inline_result_ref
from svoi_pravila.application.use_cases.get_user_by_telegram_id import (
    GetUserByTelegramIdQuery,
    GetUserByTelegramIdResult,
)
from svoi_pravila.application.use_cases.list_contacts import ListContactsCommand, ListContactsResult
from svoi_pravila.application.use_cases.list_suggestions import (
    ListSuggestionsCommand,
    ListSuggestionsResult,
)
from svoi_pravila.application.use_cases.propose_rule import ProposeRule, ProposeRuleCommand
from svoi_pravila.config import Environment, Settings, TelegramUpdatesMode
from svoi_pravila.domain.access import AccessStatus
from svoi_pravila.domain.contact import Contact
from svoi_pravila.domain.enums import (
    ConsentKind,
    Firmness,
    RelationshipKind,
    RuleCategory,
    SuggestionSource,
    SuggestionStatus,
    UsageScenario,
)
from svoi_pravila.domain.ids import ContactId, RuleSuggestionId, TelegramUserId, UserId
from svoi_pravila.domain.rule_suggestion import RuleSuggestion
from svoi_pravila.domain.rules import MAX_OPEN_RULES_PER_SCOPE, ContactScope
from svoi_pravila.domain.text import ContactLabel, RuleText
from svoi_pravila.domain.user import User as DomainUser

_NOW = datetime(2026, 10, 5, 12, tzinfo=UTC)


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


async def _add_contact(bot: Bot, lifecycle: TelegramLifecycle, user_id: int, label: str) -> None:
    base = 20_000 + user_id * 10
    await lifecycle.dispatcher.feed_update(bot, _callback(base, user_id, "ct:n"))
    await lifecycle.dispatcher.feed_update(bot, _callback(base + 1, user_id, "ct:rel:family"))
    await lifecycle.dispatcher.feed_update(bot, _text_update(base + 2, user_id, label))


def _sent_texts(session: FakeTelegramSession) -> list[str]:
    return [str(req.text) for req in session.requests if isinstance(req, SendMessage)]


@pytest.mark.unit
def test_feature_callback_registry_includes_sg() -> None:
    assert FEATURE_CALLBACK_PREFIXES == ("ct", "ru", "sg", "sn", "pr", "iv")
    assert is_feature_callback("sg:a:00000000-0000-0000-0000-000000000001")
    assert is_feature_callback("ct:n")
    assert is_feature_callback("ru:n")
    assert not is_feature_callback("age:y")
    assert not is_feature_callback(None)


@pytest.mark.unit
def test_suggestion_dm_and_callback_format() -> None:
    strings = load_ru_strings()
    text = render_suggestion_dm(
        strings,
        firmness=Firmness.GENTLE,
        contact_label="Мама",
        rule_text="Говорить мягко, без резких формулировок",
    )
    assert "мягкий" in text
    assert "Мама" in text
    assert "Говорить мягко" in text
    sid = RuleSuggestionId(UUID(int=42))
    keyboard = suggestion_decision_keyboard(strings, sid)
    data = {btn.callback_data for row in keyboard.inline_keyboard for btn in row}
    assert f"sg:a:{sid}" in data
    assert f"sg:d:{sid}" in data
    assert all(len(item.encode()) <= 64 for item in data if item is not None)


@pytest.mark.unit
async def test_chosen_inline_sends_dm_once_and_rules_shows_pending() -> None:
    uow = InMemoryUnitOfWorkFactory()
    catalog = FakeConsentCatalog()
    deps = make_telegram_deps(TelegramTestDeps(uow=uow, catalog=catalog))
    session = FakeTelegramSession()
    bot = Bot(token="1:TEST", session=session)
    lifecycle = build_telegram_lifecycle(_settings(), deps, bot=bot)
    await _onboard(bot, lifecycle, 701, catalog)
    await _add_contact(bot, lifecycle, 701, "Мама")
    session.requests.clear()
    ref = encode_inline_result_ref(UsageScenario.SOFTEN, Firmness.GENTLE)
    for index in range(5):
        await lifecycle.dispatcher.feed_update(
            bot,
            Update(
                update_id=100 + index,
                chosen_inline_result=ChosenInlineResult(
                    result_id=ref,
                    from_user=User(id=701, is_bot=False, first_name="A"),
                    query="hello world",
                ),
            ),
        )
    dm_texts = [text for text in _sent_texts(session) if "Сделать правилом" in text]
    assert len(dm_texts) == 1

    session.requests.clear()
    await lifecycle.dispatcher.feed_update(bot, _text_update(200, 701, "/rules"))
    joined = "\n".join(_sent_texts(session))
    assert "Предложения" in joined
    assert "Говорить мягко" in joined


@pytest.mark.unit
async def test_suggestion_accept_callback_creates_active_rule() -> None:
    uow = InMemoryUnitOfWorkFactory()
    catalog = FakeConsentCatalog()
    deps = make_telegram_deps(TelegramTestDeps(uow=uow, catalog=catalog))
    session = FakeTelegramSession()
    bot = Bot(token="1:TEST", session=session)
    lifecycle = build_telegram_lifecycle(_settings(), deps, bot=bot)
    await _onboard(bot, lifecycle, 702, catalog)
    await _add_contact(bot, lifecycle, 702, "Мама")
    ref = encode_inline_result_ref(UsageScenario.SOFTEN, Firmness.BALANCED)
    for index in range(5):
        await lifecycle.dispatcher.feed_update(
            bot,
            Update(
                update_id=300 + index,
                chosen_inline_result=ChosenInlineResult(
                    result_id=ref,
                    from_user=User(id=702, is_bot=False, first_name="A"),
                    query="hello world",
                ),
            ),
        )
    async with uow() as active:
        user = await active.users.get_by_telegram_id(TelegramUserId(702))
        assert user is not None
        suggestions = await active.rule_suggestions.list_for_user(user.id)
        assert len(suggestions) == 1
        suggestion_id = suggestions[0].id
        contact_id = suggestions[0].contact_id

    session.requests.clear()
    await lifecycle.dispatcher.feed_update(bot, _callback(400, 702, f"sg:a:{suggestion_id}"))
    assert any(isinstance(req, EditMessageReplyMarkup) for req in session.requests)
    async with uow() as active:
        suggestion = await active.rule_suggestions.get(suggestion_id)
        assert suggestion is not None
        assert suggestion.status is SuggestionStatus.ACCEPTED
        rules = await active.rules.list_for_scope(ContactScope(contact_id=contact_id))
        assert any(rule.status.value == "active" for rule in rules)


@pytest.mark.unit
async def test_suggestion_dismiss_callback_confirms() -> None:
    uow = InMemoryUnitOfWorkFactory()
    catalog = FakeConsentCatalog()
    deps = make_telegram_deps(TelegramTestDeps(uow=uow, catalog=catalog))
    session = FakeTelegramSession()
    bot = Bot(token="1:TEST", session=session)
    lifecycle = build_telegram_lifecycle(_settings(), deps, bot=bot)
    await _onboard(bot, lifecycle, 703, catalog)
    await _add_contact(bot, lifecycle, 703, "Мама")
    ref = encode_inline_result_ref(UsageScenario.SOFTEN, Firmness.FIRM)
    for index in range(5):
        await lifecycle.dispatcher.feed_update(
            bot,
            Update(
                update_id=500 + index,
                chosen_inline_result=ChosenInlineResult(
                    result_id=ref,
                    from_user=User(id=703, is_bot=False, first_name="A"),
                    query="hello world",
                ),
            ),
        )
    async with uow() as active:
        user = await active.users.get_by_telegram_id(TelegramUserId(703))
        assert user is not None
        suggestions = await active.rule_suggestions.list_for_user(user.id)
        suggestion_id = suggestions[0].id

    session.requests.clear()
    await lifecycle.dispatcher.feed_update(bot, _callback(600, 703, f"sg:d:{suggestion_id}"))
    assert deps.strings.suggestion_dismissed in _sent_texts(session)
    async with uow() as active:
        suggestion = await active.rule_suggestions.get(suggestion_id)
        assert suggestion is not None
        assert suggestion.status is SuggestionStatus.DISMISSED


@pytest.mark.unit
async def test_onboarding_does_not_swallow_sg_callback() -> None:
    """While onboarding is incomplete, sg: still reaches the rules router (not catch-all)."""
    uow = InMemoryUnitOfWorkFactory()
    catalog = FakeConsentCatalog()
    deps = make_telegram_deps(TelegramTestDeps(uow=uow, catalog=catalog))
    session = FakeTelegramSession()
    bot = Bot(token="1:TEST", session=session)
    lifecycle = build_telegram_lifecycle(_settings(), deps, bot=bot)
    await lifecycle.dispatcher.feed_update(bot, _text_update(1, 704, "/start"))
    session.requests.clear()
    await lifecycle.dispatcher.feed_update(
        bot, _callback(2, 704, "sg:a:00000000-0000-0000-0000-000000000099")
    )
    # Feature callback is not answered by onboarding catch-all with a re-prompt alone;
    # rules handler runs require_done and re-renders the current step.
    user = (
        await deps.get_user_by_telegram_id.execute(GetUserByTelegramIdQuery(TelegramUserId(704)))
    ).user
    assert user is None or user.age_confirmed_at is None
    assert _sent_texts(session)  # onboarding step re-shown via require_done


@pytest.mark.unit
def test_parse_suggestion_id_edges() -> None:
    assert _parse_suggestion_id(None, "a") is None
    assert _parse_suggestion_id("sg:a:not-a-uuid", "a") is None
    assert _parse_suggestion_id(f"sg:x:{UUID(int=1)}", "a") is None
    assert _parse_suggestion_id(f"ru:a:{UUID(int=1)}", "a") is None
    parsed = _parse_suggestion_id(f"sg:a:{UUID(int=7)}", "a")
    assert parsed == RuleSuggestionId(UUID(int=7))


@pytest.mark.unit
async def test_pending_suggestions_empty_without_active_contact() -> None:
    deps = make_telegram_deps(TelegramTestDeps())
    user = DomainUser(
        id=UserId(UUID(int=1)),
        telegram_user_id=TelegramUserId(1),
        created_at=_NOW,
        age_confirmed_at=_NOW,
        active_contact_id=None,
    )
    assert await _pending_suggestions(deps, user) == ()


@pytest.mark.unit
async def test_suggestion_accept_already_decided_and_limit() -> None:
    ids = FakeIdGenerator()
    uow = InMemoryUnitOfWorkFactory()
    catalog = FakeConsentCatalog()
    deps = make_telegram_deps(TelegramTestDeps(uow=uow, catalog=catalog, ids=ids))
    session = FakeTelegramSession()
    bot = Bot(token="1:TEST", session=session)
    lifecycle = build_telegram_lifecycle(_settings(), deps, bot=bot)
    await _onboard(bot, lifecycle, 705, catalog)
    await _add_contact(bot, lifecycle, 705, "Мама")
    ref = encode_inline_result_ref(UsageScenario.SOFTEN, Firmness.GENTLE)
    for index in range(5):
        await lifecycle.dispatcher.feed_update(
            bot,
            Update(
                update_id=700 + index,
                chosen_inline_result=ChosenInlineResult(
                    result_id=ref,
                    from_user=User(id=705, is_bot=False, first_name="A"),
                    query="hello world",
                ),
            ),
        )
    async with uow() as active:
        user = await active.users.get_by_telegram_id(TelegramUserId(705))
        assert user is not None
        suggestions = await active.rule_suggestions.list_for_user(user.id)
        suggestion_id = suggestions[0].id
        contact_id = suggestions[0].contact_id

    await lifecycle.dispatcher.feed_update(bot, _callback(710, 705, f"sg:d:{suggestion_id}"))
    session.requests.clear()
    await lifecycle.dispatcher.feed_update(bot, _callback(711, 705, f"sg:a:{suggestion_id}"))
    assert deps.strings.suggestion_already_decided in _sent_texts(session)
    session.requests.clear()
    await lifecycle.dispatcher.feed_update(bot, _callback(712, 705, f"sg:d:{suggestion_id}"))
    assert deps.strings.suggestion_already_decided in _sent_texts(session)

    # Prior GENTLE samples remain in the window; need enough FIRM to reach 80% dominance.
    ref2 = encode_inline_result_ref(UsageScenario.SOFTEN, Firmness.FIRM)
    for index in range(10):
        await lifecycle.dispatcher.feed_update(
            bot,
            Update(
                update_id=720 + index,
                chosen_inline_result=ChosenInlineResult(
                    result_id=ref2,
                    from_user=User(id=705, is_bot=False, first_name="A"),
                    query="hello world",
                ),
            ),
        )
    propose = ProposeRule(uow, catalog, ids, deps.clock, FakePairNotifier())
    async with uow() as active:
        user = await active.users.get_by_telegram_id(TelegramUserId(705))
        assert user is not None
        suggestions = [
            s
            for s in await active.rule_suggestions.list_for_user(user.id)
            if s.status is SuggestionStatus.PENDING
        ]
        assert len(suggestions) == 1
        pending_id = suggestions[0].id
    for index in range(MAX_OPEN_RULES_PER_SCOPE):
        await propose.execute(
            ProposeRuleCommand(
                user.id,
                contact_id,
                RuleCategory.OTHER,
                RuleText(f"limit-{index}"),
                shared=False,
            )
        )
    session.requests.clear()
    await lifecycle.dispatcher.feed_update(bot, _callback(740, 705, f"sg:a:{pending_id}"))
    assert deps.strings.rules_limit in _sent_texts(session)


@pytest.mark.unit
async def test_suggestion_callback_invalid_id_and_not_found() -> None:
    uow = InMemoryUnitOfWorkFactory()
    catalog = FakeConsentCatalog()
    deps = make_telegram_deps(TelegramTestDeps(uow=uow, catalog=catalog))
    session = FakeTelegramSession()
    bot = Bot(token="1:TEST", session=session)
    lifecycle = build_telegram_lifecycle(_settings(), deps, bot=bot)
    await _onboard(bot, lifecycle, 706, catalog)
    await _add_contact(bot, lifecycle, 706, "Мама")
    session.requests.clear()
    await lifecycle.dispatcher.feed_update(bot, _callback(800, 706, "sg:a:not-a-uuid"))
    await lifecycle.dispatcher.feed_update(bot, _callback(8001, 706, "sg:d:not-a-uuid"))
    assert deps.strings.error_generic not in _sent_texts(session)
    assert deps.strings.suggestion_already_decided not in _sent_texts(session)
    session.requests.clear()
    await lifecycle.dispatcher.feed_update(bot, _callback(801, 706, f"sg:a:{UUID(int=999)}"))
    assert deps.strings.error_generic in _sent_texts(session)
    session.requests.clear()
    await lifecycle.dispatcher.feed_update(bot, _callback(802, 706, f"sg:d:{UUID(int=999)}"))
    assert deps.strings.error_generic in _sent_texts(session)


class _HideUser:
    def __init__(self, inner: object) -> None:
        self._inner = inner

    async def execute(self, query: GetUserByTelegramIdQuery) -> GetUserByTelegramIdResult:
        await cast(Any, self._inner).execute(query)
        return GetUserByTelegramIdResult(user=None)


@pytest.mark.unit
async def test_suggestion_callback_user_gone_after_done() -> None:
    uow = InMemoryUnitOfWorkFactory()
    catalog = FakeConsentCatalog()
    deps = make_telegram_deps(TelegramTestDeps(uow=uow, catalog=catalog))
    session = FakeTelegramSession()
    bot = Bot(token="1:TEST", session=session)
    lifecycle = build_telegram_lifecycle(_settings(), deps, bot=bot)
    await _onboard(bot, lifecycle, 707, catalog)
    await _add_contact(bot, lifecycle, 707, "Мама")
    hidden = replace(
        deps, get_user_by_telegram_id=cast(Any, _HideUser(deps.get_user_by_telegram_id))
    )
    lifecycle = build_telegram_lifecycle(_settings(), hidden, bot=bot)
    session.requests.clear()
    await lifecycle.dispatcher.feed_update(bot, _callback(810, 707, f"sg:a:{UUID(int=1)}"))
    await lifecycle.dispatcher.feed_update(bot, _callback(811, 707, f"sg:d:{UUID(int=1)}"))
    assert _sent_texts(session)


@pytest.mark.unit
async def test_tone_suggestion_dm_early_returns_and_api_error() -> None:
    suggestion_id = RuleSuggestionId(UUID(int=42))
    bot = Bot(token="1:TEST", session=FakeTelegramSession())

    class _Lookup:
        def __init__(self, user: DomainUser | None) -> None:
            self._user = user

        async def execute(self, query: GetUserByTelegramIdQuery) -> GetUserByTelegramIdResult:
            return GetUserByTelegramIdResult(user=self._user)

    class _ListSug:
        def __init__(self, suggestions: tuple[RuleSuggestion, ...] = ()) -> None:
            self._suggestions = suggestions

        async def execute(self, command: ListSuggestionsCommand) -> ListSuggestionsResult:
            return ListSuggestionsResult(suggestions=self._suggestions)

    class _ListContacts:
        def __init__(self, contacts: tuple[Contact, ...] = ()) -> None:
            self._contacts = contacts

        async def execute(self, command: ListContactsCommand) -> ListContactsResult:
            return ListContactsResult(contacts=self._contacts)

    class _BoomList:
        async def execute(self, command: ListSuggestionsCommand) -> ListSuggestionsResult:
            raise NotFound()

    class _DeniedList:
        async def execute(self, command: ListSuggestionsCommand) -> ListSuggestionsResult:
            raise AccessNotGranted(
                AccessStatus(age_confirmed=True, missing_consents=frozenset(), granted=False)
            )

    base = make_telegram_deps(TelegramTestDeps())
    await _send_tone_suggestion_dm(
        bot, replace(base, get_user_by_telegram_id=cast(Any, _Lookup(None))), 1, suggestion_id
    )

    bare = DomainUser(
        id=UserId(UUID(int=2)),
        telegram_user_id=TelegramUserId(2),
        created_at=_NOW,
        age_confirmed_at=_NOW,
        active_contact_id=None,
    )
    await _send_tone_suggestion_dm(
        bot, replace(base, get_user_by_telegram_id=cast(Any, _Lookup(bare))), 2, suggestion_id
    )

    with_contact = DomainUser(
        id=UserId(UUID(int=3)),
        telegram_user_id=TelegramUserId(3),
        created_at=_NOW,
        age_confirmed_at=_NOW,
        active_contact_id=ContactId(UUID(int=3)),
    )
    await _send_tone_suggestion_dm(
        bot,
        replace(
            base,
            get_user_by_telegram_id=cast(Any, _Lookup(with_contact)),
            list_suggestions=cast(Any, _ListSug(())),
        ),
        3,
        suggestion_id,
    )

    orphan = RuleSuggestion.create_tone(
        suggestion_id=suggestion_id,
        user_id=with_contact.id,
        contact_id=ContactId(UUID(int=3)),
        category=RuleCategory.HOW_TO_ASK,
        text=RuleText("Говорить мягко, без резких формулировок"),
        firmness=Firmness.GENTLE,
        now=_NOW,
    )
    await _send_tone_suggestion_dm(
        bot,
        replace(
            base,
            get_user_by_telegram_id=cast(Any, _Lookup(with_contact)),
            list_suggestions=cast(Any, _ListSug((orphan,))),
            list_contacts=cast(Any, _ListContacts(())),
        ),
        3,
        suggestion_id,
    )

    no_firm = RuleSuggestion(
        id=suggestion_id,
        user_id=with_contact.id,
        contact_id=ContactId(UUID(int=3)),
        source=SuggestionSource.DECODE,
        category=RuleCategory.HOW_TO_ASK,
        text=RuleText("Декод-заготовка без тона"),
        firmness=None,
        status=SuggestionStatus.PENDING,
        created_at=_NOW,
        decided_at=None,
    )
    await _send_tone_suggestion_dm(
        bot,
        replace(
            base,
            get_user_by_telegram_id=cast(Any, _Lookup(with_contact)),
            list_suggestions=cast(Any, _ListSug((no_firm,))),
        ),
        3,
        suggestion_id,
    )

    contact = Contact(
        id=ContactId(UUID(int=3)),
        owner_id=with_contact.id,
        label=ContactLabel("Мама"),
        relationship=RelationshipKind.FAMILY,
        created_at=_NOW,
        pair_id=None,
    )
    failing = AsyncMock(
        side_effect=TelegramAPIError(method=SendMessage(chat_id=3, text="x"), message="fail")
    )
    object.__setattr__(bot, "send_message", failing)
    await _send_tone_suggestion_dm(
        bot,
        replace(
            base,
            get_user_by_telegram_id=cast(Any, _Lookup(with_contact)),
            list_suggestions=cast(Any, _ListSug((orphan,))),
            list_contacts=cast(Any, _ListContacts((contact,))),
        ),
        3,
        suggestion_id,
    )
    await _send_tone_suggestion_dm(
        bot,
        replace(
            base,
            get_user_by_telegram_id=cast(Any, _Lookup(with_contact)),
            list_suggestions=cast(Any, _BoomList()),
        ),
        3,
        suggestion_id,
    )
    await _send_tone_suggestion_dm(
        bot,
        replace(
            base,
            get_user_by_telegram_id=cast(Any, _Lookup(with_contact)),
            list_suggestions=cast(Any, _DeniedList()),
        ),
        3,
        suggestion_id,
    )


_CANARY_LABEL = "CANARY_TONE_SUG_0011"


@pytest.mark.unit
async def test_tone_suggestion_dm_privacy_canary(
    capture_log_events: Callable[[], list[dict[str, Any]]],
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    uow = InMemoryUnitOfWorkFactory()
    catalog = FakeConsentCatalog()
    deps = make_telegram_deps(TelegramTestDeps(uow=uow, catalog=catalog))
    session = FakeTelegramSession()
    bot = Bot(token="1:TEST", session=session)
    lifecycle = build_telegram_lifecycle(_settings(), deps, bot=bot)
    await _onboard(bot, lifecycle, 708, catalog)
    await _add_contact(bot, lifecycle, 708, _CANARY_LABEL)
    ref = encode_inline_result_ref(UsageScenario.SOFTEN, Firmness.BALANCED)
    for index in range(5):
        await lifecycle.dispatcher.feed_update(
            bot,
            Update(
                update_id=900 + index,
                chosen_inline_result=ChosenInlineResult(
                    result_id=ref,
                    from_user=User(id=708, is_bot=False, first_name="A"),
                    query="hello world",
                ),
            ),
        )
    dm_texts = [text for text in _sent_texts(session) if "Сделать правилом" in text]
    assert len(dm_texts) == 1
    assert _CANARY_LABEL in dm_texts[0]
    events = capture_log_events()
    blob = json.dumps(events, default=str) + "\n".join(caplog.messages)
    assert _CANARY_LABEL not in blob
    assert "Говорить спокойно" not in blob
