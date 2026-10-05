"""Integration: invite → accept → shared propose → approve → leave on Postgres."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from svoi_pravila.adapters.persistence.uow import SqlAlchemyUnitOfWorkFactory
from svoi_pravila.application.use_cases.accept_age_confirmation import (
    AcceptAgeConfirmation,
    AcceptAgeConfirmationCommand,
)
from svoi_pravila.application.use_cases.accept_invite import AcceptInvite, AcceptInviteCommand
from svoi_pravila.application.use_cases.approve_rule import ApproveRule, ApproveRuleCommand
from svoi_pravila.application.use_cases.create_contact import CreateContact, CreateContactCommand
from svoi_pravila.application.use_cases.create_invite import CreateInvite, CreateInviteCommand
from svoi_pravila.application.use_cases.grant_consent import GrantConsent, GrantConsentCommand
from svoi_pravila.application.use_cases.leave_pair import LeavePair, LeavePairCommand
from svoi_pravila.application.use_cases.propose_rule import ProposeRule, ProposeRuleCommand
from svoi_pravila.application.use_cases.resolve_invite import ResolveInvite, ResolveInviteCommand
from svoi_pravila.domain.enums import ConsentKind, RelationshipKind, RuleCategory, RuleStatus
from svoi_pravila.domain.ids import TelegramUserId
from svoi_pravila.domain.rules import ContactScope, PairScope
from svoi_pravila.domain.text import ContactLabel, RuleText
from svoi_pravila.domain.user import User
from tests.fakes.clock import FakeClock
from tests.fakes.consent_catalog import FakeConsentCatalog
from tests.fakes.ids import FakeIdGenerator
from tests.fakes.pair_notifier import FakePairNotifier
from tests.fakes.tokens import FakeTokenGenerator

_NOW = datetime(2026, 4, 1, tzinfo=UTC)


async def _granted(
    uow_factory_postgres: SqlAlchemyUnitOfWorkFactory,
    catalog: FakeConsentCatalog,
    ids: FakeIdGenerator,
    clock: FakeClock,
    telegram_id: int,
) -> User:
    accepted = await AcceptAgeConfirmation(uow_factory_postgres, ids, clock).execute(
        AcceptAgeConfirmationCommand(TelegramUserId(telegram_id))
    )
    for kind in ConsentKind:
        version = catalog.current_requirement().for_kind(kind).version
        await GrantConsent(uow_factory_postgres, catalog, ids, clock).execute(
            GrantConsentCommand(accepted.user.id, kind, version)
        )
    return accepted.user


@pytest.mark.integration
async def test_invite_accept_shared_approve_leave_cycle(
    uow_factory_postgres: SqlAlchemyUnitOfWorkFactory,
) -> None:
    catalog = FakeConsentCatalog()
    clock = FakeClock(start=_NOW)
    ids = FakeIdGenerator()
    tokens = FakeTokenGenerator()
    notifier = FakePairNotifier()
    inviter = await _granted(uow_factory_postgres, catalog, ids, clock, 1601)
    invitee = await _granted(uow_factory_postgres, catalog, ids, clock, 1602)
    contact = (
        await CreateContact(uow_factory_postgres, catalog, ids, clock).execute(
            CreateContactCommand(inviter.id, ContactLabel("Partner"), RelationshipKind.PARTNER)
        )
    ).contact
    created = await CreateInvite(uow_factory_postgres, catalog, ids, tokens, clock).execute(
        CreateInviteCommand(inviter.id, contact.id)
    )
    resolved = await ResolveInvite(uow_factory_postgres, catalog, clock).execute(
        ResolveInviteCommand(invitee.id, created.raw_token)
    )
    accepted = await AcceptInvite(uow_factory_postgres, catalog, ids, clock, notifier).execute(
        AcceptInviteCommand(
            invitee.id,
            resolved.invite_id,
            ContactLabel("Inviter"),
            RelationshipKind.PARTNER,
        )
    )
    assert notifier.invite_accepted_calls
    shared = await ProposeRule(uow_factory_postgres, catalog, ids, clock, notifier).execute(
        ProposeRuleCommand(
            inviter.id,
            contact.id,
            RuleCategory.TABOO_TOPIC,
            RuleText("shared integration rule"),
            shared=True,
        )
    )
    assert shared.rule.status is RuleStatus.PROPOSED
    assert notifier.shared_rule_proposed_calls
    approved = await ApproveRule(uow_factory_postgres, catalog, clock, notifier).execute(
        ApproveRuleCommand(invitee.id, shared.rule.id)
    )
    assert approved.rule.status is RuleStatus.ACTIVE
    assert notifier.shared_rule_decided_calls
    await LeavePair(uow_factory_postgres, ids, clock, notifier).execute(
        LeavePairCommand(invitee.id, accepted.pair.id)
    )
    assert notifier.partner_left_calls
    async with uow_factory_postgres() as uow:
        assert await uow.pairs.get(accepted.pair.id) is None
        remaining = await uow.rules.list_for_scope(ContactScope(contact_id=contact.id))
        texts = {rule.revisions[-1].text.value for rule in remaining}
        assert "shared integration rule" in texts
        assert all(not isinstance(rule.scope, PairScope) for rule in remaining)
