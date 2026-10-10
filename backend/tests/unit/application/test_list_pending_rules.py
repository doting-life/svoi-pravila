"""ListPendingRules aggregates items awaiting the actor's decision."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest

from svoi_pravila.application.errors import RuleTextTooLong
from svoi_pravila.application.use_cases.accept_invite import (
    AcceptInvite,
    AcceptInviteCommand,
    AcceptInviteResult,
)
from svoi_pravila.application.use_cases.approve_rule import ApproveRule, ApproveRuleCommand
from svoi_pravila.application.use_cases.create_contact import CreateContact, CreateContactCommand
from svoi_pravila.application.use_cases.create_invite import CreateInvite, CreateInviteCommand
from svoi_pravila.application.use_cases.list_pending_rules import (
    ListPendingRules,
    ListPendingRulesQuery,
    needs_actor_decision,
)
from svoi_pravila.application.use_cases.propose_rule import ProposeRule, ProposeRuleCommand
from svoi_pravila.application.use_cases.propose_rule_edit import (
    ProposeRuleEdit,
    ProposeRuleEditCommand,
)
from svoi_pravila.domain.contact import Contact
from svoi_pravila.domain.enums import RelationshipKind, RuleCategory, RuleStatus
from svoi_pravila.domain.ids import ContactId, PairId, RuleId, UserId
from svoi_pravila.domain.rules import ContactScope, PairScope, Rule
from svoi_pravila.domain.text import RULE_TEXT_MAX_CHARS, ContactLabel, RuleText
from svoi_pravila.domain.user import User
from tests.unit.application.conftest import AppWorld

_NOW = datetime(2026, 1, 1, tzinfo=UTC)
_OWNER = UserId(UUID(int=1))
_PARTNER = UserId(UUID(int=2))
_STRANGER = UserId(UUID(int=3))


async def _pair(world: AppWorld) -> tuple[User, User, Contact, AcceptInviteResult]:
    inviter = await world.ensure_granted_user(20)
    invitee = await world.ensure_granted_user(21)
    contact = (
        await CreateContact(world.uow_factory, world.catalog, world.ids, world.clock).execute(
            CreateContactCommand(inviter.id, ContactLabel("Partner"), RelationshipKind.PARTNER)
        )
    ).contact
    invite = await CreateInvite(
        world.uow_factory, world.catalog, world.ids, world.tokens, world.clock
    ).execute(CreateInviteCommand(inviter.id, contact.id))
    accepted = await AcceptInvite(
        world.uow_factory, world.catalog, world.ids, world.clock, world.notifier
    ).execute(
        AcceptInviteCommand(
            invitee.id,
            invite.invite.id,
            ContactLabel("Inviter"),
            RelationshipKind.PARTNER,
        )
    )
    return inviter, invitee, contact, accepted


@pytest.mark.unit
def test_needs_actor_decision_rejects_non_approvers_and_private_scope() -> None:
    private = Rule.propose(
        rule_id=RuleId(UUID(int=10)),
        scope=ContactScope(contact_id=ContactId(UUID(int=20))),
        category=RuleCategory.OTHER,
        approvers=frozenset({_OWNER}),
        author_id=_OWNER,
        text=RuleText("личное"),
        now=_NOW,
    )
    assert needs_actor_decision(private, _OWNER) is False

    proposed = Rule.propose(
        rule_id=RuleId(UUID(int=11)),
        scope=PairScope(pair_id=PairId(UUID(int=21))),
        category=RuleCategory.OTHER,
        approvers=frozenset({_OWNER, _PARTNER}),
        author_id=_OWNER,
        text=RuleText("общее"),
        now=_NOW,
    )
    assert needs_actor_decision(proposed, _STRANGER) is False
    assert needs_actor_decision(proposed, _PARTNER) is True

    active = proposed.approve(_PARTNER, _NOW)
    assert active.pending_revision is None
    assert needs_actor_decision(active, _PARTNER) is False

    rejected = proposed.reject_pending(_PARTNER, _NOW)
    assert rejected.status is RuleStatus.REJECTED
    assert needs_actor_decision(rejected, _PARTNER) is False


@pytest.mark.unit
async def test_needs_actor_decision_covers_proposed_and_pending_edit(world: AppWorld) -> None:
    inviter, invitee, contact, _accepted = await _pair(world)
    proposed = await ProposeRule(
        world.uow_factory, world.catalog, world.ids, world.clock, world.notifier
    ).execute(
        ProposeRuleCommand(
            actor_id=inviter.id,
            contact_id=contact.id,
            category=RuleCategory.OTHER,
            text=RuleText("мы не кричим"),
            shared=True,
        )
    )
    assert needs_actor_decision(proposed.rule, invitee.id) is True
    assert needs_actor_decision(proposed.rule, inviter.id) is False

    approved = await ApproveRule(
        world.uow_factory, world.catalog, world.clock, world.notifier
    ).execute(ApproveRuleCommand(invitee.id, proposed.rule.id))
    edited = await ProposeRuleEdit(world.uow_factory, world.catalog, world.clock).execute(
        ProposeRuleEditCommand(inviter.id, approved.rule.id, RuleText("мы говорим спокойно"))
    )
    assert edited.rule.status is RuleStatus.ACTIVE
    assert needs_actor_decision(edited.rule, invitee.id) is True
    assert needs_actor_decision(edited.rule, inviter.id) is False


@pytest.mark.unit
def test_rule_text_too_long_application_error_carries_bounds() -> None:
    err = RuleTextTooLong(maximum=RULE_TEXT_MAX_CHARS, actual=RULE_TEXT_MAX_CHARS + 1)
    assert err.max == RULE_TEXT_MAX_CHARS
    assert err.actual == RULE_TEXT_MAX_CHARS + 1


@pytest.mark.unit
async def test_list_pending_rules_returns_partner_items_only(world: AppWorld) -> None:
    inviter, invitee, contact, accepted = await _pair(world)
    await ProposeRule(
        world.uow_factory, world.catalog, world.ids, world.clock, world.notifier
    ).execute(
        ProposeRuleCommand(
            actor_id=inviter.id,
            contact_id=contact.id,
            category=RuleCategory.OTHER,
            text=RuleText("общее правило\nвторой абзац"),
            shared=True,
        )
    )
    use_case = ListPendingRules(world.uow_factory, world.catalog)
    for_invitee = await use_case.execute(ListPendingRulesQuery(actor_id=invitee.id))
    assert len(for_invitee.items) == 1
    item = for_invitee.items[0]
    assert item.contact_id == accepted.invitee_contact.id
    assert "общее правило" in item.text.value
    assert item.shared is True

    for_inviter = await use_case.execute(ListPendingRulesQuery(actor_id=inviter.id))
    assert for_inviter.items == ()

    stranger = await world.ensure_granted_user(999)
    for_stranger = await use_case.execute(ListPendingRulesQuery(actor_id=stranger.id))
    assert for_stranger.items == ()


@pytest.mark.unit
async def test_list_pending_rules_skips_unpaired_and_dedupes_pair_contacts(
    world: AppWorld,
) -> None:
    inviter, invitee, contact, accepted = await _pair(world)
    await CreateContact(world.uow_factory, world.catalog, world.ids, world.clock).execute(
        CreateContactCommand(invitee.id, ContactLabel("Одинокий"), RelationshipKind.FRIEND)
    )
    proposed = await ProposeRule(
        world.uow_factory, world.catalog, world.ids, world.clock, world.notifier
    ).execute(
        ProposeRuleCommand(
            actor_id=inviter.id,
            contact_id=contact.id,
            category=RuleCategory.OTHER,
            text=RuleText("ждёт ответа"),
            shared=True,
        )
    )
    assert accepted.invitee_contact.pair_id is not None
    async with world.uow_factory() as uow:
        await uow.contacts.add(
            Contact(
                id=ContactId(UUID(int=9001)),
                owner_id=invitee.id,
                label=ContactLabel("Дубль"),
                relationship=RelationshipKind.PARTNER,
                pair_id=accepted.invitee_contact.pair_id,
                created_at=_NOW,
            )
        )
        await uow.commit()

    use_case = ListPendingRules(world.uow_factory, world.catalog)
    items = await use_case.execute(ListPendingRulesQuery(actor_id=invitee.id))
    assert len(items.items) == 1
    assert items.items[0].rule_id == proposed.rule.id
