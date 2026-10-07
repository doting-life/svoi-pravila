"""Unit tests for TelegramPairNotifier delivery and failure isolation."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

import pytest
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.methods import SendMessage
from aiogram.types import InlineKeyboardMarkup, WebAppInfo
from tests.fakes.clock import FakeClock
from tests.fakes.consent_catalog import FakeConsentCatalog
from tests.fakes.ids import FakeIdGenerator
from tests.fakes.pair_notifier import FakePairNotifier
from tests.fakes.telegram_session import FakeTelegramSession
from tests.fakes.tokens import FakeTokenGenerator
from tests.fakes.uow import InMemoryUnitOfWorkFactory

from svoi_pravila.adapters.channels.telegram.localization import TelegramStrings, load_ru_strings
from svoi_pravila.adapters.channels.telegram.pair_notifier import TelegramPairNotifier
from svoi_pravila.application.ports.unit_of_work import UnitOfWorkFactory
from svoi_pravila.application.use_cases.accept_age_confirmation import (
    AcceptAgeConfirmation,
    AcceptAgeConfirmationCommand,
)
from svoi_pravila.application.use_cases.accept_invite import AcceptInvite, AcceptInviteCommand
from svoi_pravila.application.use_cases.create_contact import CreateContact, CreateContactCommand
from svoi_pravila.application.use_cases.create_invite import CreateInvite, CreateInviteCommand
from svoi_pravila.application.use_cases.grant_consent import GrantConsent, GrantConsentCommand
from svoi_pravila.application.use_cases.propose_rule import ProposeRule, ProposeRuleCommand
from svoi_pravila.domain.enums import ConsentKind, RelationshipKind, RuleCategory
from svoi_pravila.domain.ids import ContactId, RuleId, TelegramUserId, UserId
from svoi_pravila.domain.text import ContactLabel, RuleText

_NOW = datetime(2026, 4, 1, tzinfo=UTC)
_MINIAPP = "https://miniapp.example"


@pytest.fixture
def world() -> tuple[
    InMemoryUnitOfWorkFactory,
    FakeConsentCatalog,
    FakeIdGenerator,
    FakeClock,
    FakeTokenGenerator,
]:
    return (
        InMemoryUnitOfWorkFactory(),
        FakeConsentCatalog(),
        FakeIdGenerator(),
        FakeClock(start=_NOW),
        FakeTokenGenerator(),
    )


async def _grant(
    uow: InMemoryUnitOfWorkFactory,
    catalog: FakeConsentCatalog,
    ids: FakeIdGenerator,
    clock: FakeClock,
    telegram_id: int,
) -> UserId:
    accepted = await AcceptAgeConfirmation(uow, ids, clock).execute(
        AcceptAgeConfirmationCommand(TelegramUserId(telegram_id))
    )
    for kind in ConsentKind:
        version = catalog.current_requirement().for_kind(kind).version
        await GrantConsent(uow, catalog, ids, clock).execute(
            GrantConsentCommand(accepted.user.id, kind, version)
        )
    return accepted.user.id


def _assert_web_app(req: SendMessage, strings: TelegramStrings) -> None:
    markup = req.reply_markup
    assert isinstance(markup, InlineKeyboardMarkup)
    button = markup.inline_keyboard[0][0]
    assert button.text == strings.dm_open_app
    assert isinstance(button.web_app, WebAppInfo)
    assert button.web_app.url == _MINIAPP


@pytest.mark.unit
async def test_telegram_pair_notifier_happy_paths_and_guards(
    world: tuple[
        InMemoryUnitOfWorkFactory,
        FakeConsentCatalog,
        FakeIdGenerator,
        FakeClock,
        FakeTokenGenerator,
    ],
) -> None:
    uow, catalog, ids, clock, tokens = world
    strings = load_ru_strings()
    session = FakeTelegramSession()
    bot = Bot(token="1:TEST", session=session)
    notifier = TelegramPairNotifier(bot, uow, strings, miniapp_url=_MINIAPP)
    inviter_id = await _grant(uow, catalog, ids, clock, 501)
    invitee_id = await _grant(uow, catalog, ids, clock, 502)
    contact = (
        await CreateContact(uow, catalog, ids, clock).execute(
            CreateContactCommand(inviter_id, ContactLabel("Partner"), RelationshipKind.PARTNER)
        )
    ).contact
    created = await CreateInvite(uow, catalog, ids, tokens, clock).execute(
        CreateInviteCommand(inviter_id, contact.id)
    )
    await AcceptInvite(uow, catalog, ids, clock, FakePairNotifier()).execute(
        AcceptInviteCommand(
            invitee_id, created.invite.id, ContactLabel("Inviter"), RelationshipKind.PARTNER
        )
    )
    await notifier.invite_accepted(inviter_id, contact.id)
    accepted_msgs = [
        req
        for req in session.requests
        if isinstance(req, SendMessage) and req.text == strings.pair_invite_accepted
    ]
    assert len(accepted_msgs) == 1
    _assert_web_app(accepted_msgs[0], strings)
    assert "Partner" not in str(accepted_msgs[0].text)

    shared = await ProposeRule(uow, catalog, ids, clock, FakePairNotifier()).execute(
        ProposeRuleCommand(
            inviter_id,
            contact.id,
            RuleCategory.OTHER,
            RuleText("shared for dm"),
            shared=True,
        )
    )
    await notifier.shared_rule_proposed(invitee_id, shared.rule.id)
    proposed = [
        req
        for req in session.requests
        if isinstance(req, SendMessage) and req.text == strings.pair_shared_rule_proposed
    ]
    assert proposed
    assert "shared for dm" not in str(proposed[-1].text)
    _assert_web_app(proposed[-1], strings)

    await notifier.shared_rule_decided(inviter_id, shared.rule.id, approved=True)
    await notifier.shared_rule_decided(inviter_id, shared.rule.id, approved=False)
    await notifier.partner_left(inviter_id, contact.id)

    before = len(session.requests)
    await notifier.invite_accepted(UserId(UUID(int=999)), contact.id)
    await notifier.shared_rule_proposed(UserId(UUID(int=996)), shared.rule.id)
    await notifier.shared_rule_decided(UserId(UUID(int=996)), shared.rule.id, approved=True)
    await notifier.partner_left(UserId(UUID(int=995)), contact.id)
    assert len(session.requests) == before


@pytest.mark.unit
async def test_telegram_pair_notifier_swallows_telegram_api_errors(
    world: tuple[
        InMemoryUnitOfWorkFactory,
        FakeConsentCatalog,
        FakeIdGenerator,
        FakeClock,
        FakeTokenGenerator,
    ],
    capture_log_events: Callable[[], list[dict[str, object]]],
) -> None:
    uow, catalog, ids, clock, _tokens = world
    strings = load_ru_strings()
    inviter_id = await _grant(uow, catalog, ids, clock, 601)
    contact = (
        await CreateContact(uow, catalog, ids, clock).execute(
            CreateContactCommand(inviter_id, ContactLabel("X"), RelationshipKind.FRIEND)
        )
    ).contact

    for exc in (
        TelegramForbiddenError(
            method=SendMessage(chat_id=1, text="x"),
            message="Forbidden: bot was blocked by the user",
        ),
        TelegramBadRequest(
            method=SendMessage(chat_id=1, text="x"),
            message="Bad Request: chat not found",
        ),
    ):
        session = FakeTelegramSession()
        session.set_error(SendMessage, exc)
        bot = Bot(token="1:TEST", session=session)
        notifier = TelegramPairNotifier(bot, uow, strings, miniapp_url=_MINIAPP)
        await notifier.invite_accepted(inviter_id, contact.id)
        await notifier.partner_left(inviter_id, contact.id)

    events = capture_log_events()
    failed = [event for event in events if event.get("event") == "pair_notifier_failed"]
    assert failed
    assert {event.get("action") for event in failed} >= {"invite_accepted", "partner_left"}
    assert {event.get("error_type") for event in failed} >= {
        "TelegramForbiddenError",
        "TelegramBadRequest",
    }


@pytest.mark.unit
async def test_telegram_pair_notifier_propagates_non_telegram_errors(
    world: tuple[
        InMemoryUnitOfWorkFactory,
        FakeConsentCatalog,
        FakeIdGenerator,
        FakeClock,
        FakeTokenGenerator,
    ],
) -> None:
    _uow, _catalog, _ids, _clock, _tokens = world
    strings = load_ru_strings()
    session = FakeTelegramSession()
    bot = Bot(token="1:TEST", session=session)

    class _BoomFactory:
        def __call__(self) -> object:
            raise RuntimeError("uow boom")

    boom = TelegramPairNotifier(
        bot, cast(UnitOfWorkFactory, _BoomFactory()), strings, miniapp_url=_MINIAPP
    )
    with pytest.raises(RuntimeError, match="uow boom"):
        await boom.invite_accepted(UserId(UUID(int=1)), ContactId(UUID(int=2)))
    with pytest.raises(RuntimeError, match="uow boom"):
        await boom.shared_rule_proposed(UserId(UUID(int=1)), RuleId(UUID(int=1)))
    with pytest.raises(RuntimeError, match="uow boom"):
        await boom.shared_rule_decided(UserId(UUID(int=1)), RuleId(UUID(int=1)), approved=False)
    with pytest.raises(RuntimeError, match="uow boom"):
        await boom.partner_left(UserId(UUID(int=1)), ContactId(UUID(int=2)))
