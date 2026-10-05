"""Unit tests for TelegramPairNotifier delivery and failure isolation."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import cast
from uuid import UUID

import pytest
from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.methods import SendMessage
from tests.fakes.clock import FakeClock
from tests.fakes.consent_catalog import FakeConsentCatalog
from tests.fakes.ids import FakeIdGenerator
from tests.fakes.pair_notifier import FakePairNotifier
from tests.fakes.telegram_session import FakeTelegramSession
from tests.fakes.tokens import FakeTokenGenerator
from tests.fakes.uow import InMemoryUnitOfWorkFactory

from svoi_pravila.adapters.channels.telegram.localization import load_ru_strings
from svoi_pravila.adapters.channels.telegram.pair_notifier import TelegramPairNotifier
from svoi_pravila.application.ports.unit_of_work import UnitOfWorkFactory
from svoi_pravila.application.use_cases.accept_invite import AcceptInvite, AcceptInviteCommand
from svoi_pravila.application.use_cases.create_contact import CreateContact, CreateContactCommand
from svoi_pravila.application.use_cases.create_invite import CreateInvite, CreateInviteCommand
from svoi_pravila.application.use_cases.propose_rule import ProposeRule, ProposeRuleCommand
from svoi_pravila.domain.enums import RelationshipKind, RuleCategory
from svoi_pravila.domain.ids import ContactId, RuleId, UserId
from svoi_pravila.domain.rules import ContactScope, Rule
from svoi_pravila.domain.text import ContactLabel, RuleText

_NOW = datetime(2026, 4, 1, tzinfo=UTC)


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
    from svoi_pravila.application.use_cases.accept_age_confirmation import (
        AcceptAgeConfirmation,
        AcceptAgeConfirmationCommand,
    )
    from svoi_pravila.application.use_cases.grant_consent import GrantConsent, GrantConsentCommand
    from svoi_pravila.domain.enums import ConsentKind
    from svoi_pravila.domain.ids import TelegramUserId

    accepted = await AcceptAgeConfirmation(uow, ids, clock).execute(
        AcceptAgeConfirmationCommand(TelegramUserId(telegram_id))
    )
    for kind in ConsentKind:
        version = catalog.current_requirement().for_kind(kind).version
        await GrantConsent(uow, catalog, ids, clock).execute(
            GrantConsentCommand(accepted.user.id, kind, version)
        )
    return accepted.user.id


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
    notifier = TelegramPairNotifier(bot, uow, strings)
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
    assert any(
        isinstance(req, SendMessage) and "Partner" in str(req.text) for req in session.requests
    )

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
        req for req in session.requests if isinstance(req, SendMessage) and req.reply_markup
    ]
    assert proposed
    assert "shared for dm" in str(proposed[-1].text)

    await notifier.shared_rule_decided(inviter_id, shared.rule.id, approved=True)
    await notifier.shared_rule_decided(inviter_id, shared.rule.id, approved=False)
    await notifier.partner_left(inviter_id, contact.id)

    await notifier.invite_accepted(UserId(UUID(int=999)), contact.id)
    await notifier.invite_accepted(inviter_id, ContactId(UUID(int=998)))
    await notifier.shared_rule_proposed(invitee_id, RuleId(UUID(int=997)))
    await notifier.shared_rule_decided(UserId(UUID(int=996)), shared.rule.id, approved=True)
    await notifier.partner_left(UserId(UUID(int=995)), contact.id)

    private = Rule.propose(
        rule_id=RuleId(UUID(int=94)),
        scope=ContactScope(contact_id=contact.id),
        category=RuleCategory.OTHER,
        approvers=frozenset({inviter_id}),
        author_id=inviter_id,
        text=RuleText("private only"),
        now=clock.now(),
    )
    async with uow() as unit:
        await unit.rules.add(private)
        await unit.commit()
    await notifier.shared_rule_proposed(invitee_id, private.id)

    approved_shared = await ProposeRule(uow, catalog, ids, clock, FakePairNotifier()).execute(
        ProposeRuleCommand(
            inviter_id,
            contact.id,
            RuleCategory.OTHER,
            RuleText("already active"),
            shared=True,
        )
    )
    from svoi_pravila.application.use_cases.approve_rule import ApproveRule, ApproveRuleCommand

    await ApproveRule(uow, catalog, clock, FakePairNotifier()).execute(
        ApproveRuleCommand(invitee_id, approved_shared.rule.id)
    )
    await notifier.shared_rule_proposed(invitee_id, approved_shared.rule.id)


@pytest.mark.unit
async def test_telegram_pair_notifier_logs_send_and_load_failures(
    world: tuple[
        InMemoryUnitOfWorkFactory,
        FakeConsentCatalog,
        FakeIdGenerator,
        FakeClock,
        FakeTokenGenerator,
    ],
) -> None:
    uow, catalog, ids, clock, _tokens = world
    strings = load_ru_strings()
    session = FakeTelegramSession()
    session.set_error(
        SendMessage,
        TelegramAPIError(method=SendMessage(chat_id=1, text="x"), message="fail"),
    )
    bot = Bot(token="1:TEST", session=session)
    notifier = TelegramPairNotifier(bot, uow, strings)
    inviter_id = await _grant(uow, catalog, ids, clock, 601)
    contact = (
        await CreateContact(uow, catalog, ids, clock).execute(
            CreateContactCommand(inviter_id, ContactLabel("X"), RelationshipKind.FRIEND)
        )
    ).contact
    await notifier.invite_accepted(inviter_id, contact.id)
    await notifier.partner_left(inviter_id, contact.id)

    class _BoomFactory:
        def __call__(self) -> object:
            raise RuntimeError("uow boom")

    boom = TelegramPairNotifier(bot, cast(UnitOfWorkFactory, _BoomFactory()), strings)
    await boom.invite_accepted(inviter_id, contact.id)
    await boom.shared_rule_proposed(inviter_id, RuleId(UUID(int=1)))
    await boom.shared_rule_decided(inviter_id, RuleId(UUID(int=1)), approved=False)
    await boom.partner_left(inviter_id, contact.id)
