"""Rule and invite use-case tests including authorization and privacy."""

from __future__ import annotations

from datetime import timedelta
from uuid import UUID

import pytest

from svoi_pravila.application.errors import (
    AlreadyPaired,
    ContactAlreadyLinked,
    ContactLimitReached,
    NotFound,
    OpenRuleLimitReached,
)
from svoi_pravila.application.use_cases.accept_invite import (
    AcceptInvite,
    AcceptInviteCommand,
    AcceptInviteResult,
)
from svoi_pravila.application.use_cases.approve_rule import ApproveRule, ApproveRuleCommand
from svoi_pravila.application.use_cases.archive_rule import ArchiveRule, ArchiveRuleCommand
from svoi_pravila.application.use_cases.create_contact import (
    CreateContact,
    CreateContactCommand,
)
from svoi_pravila.application.use_cases.create_invite import CreateInvite, CreateInviteCommand
from svoi_pravila.application.use_cases.get_effective_rules import (
    GetEffectiveRules,
    GetEffectiveRulesCommand,
)
from svoi_pravila.application.use_cases.leave_pair import LeavePair, LeavePairCommand
from svoi_pravila.application.use_cases.list_rules import ListRules, ListRulesCommand
from svoi_pravila.application.use_cases.propose_rule import ProposeRule, ProposeRuleCommand
from svoi_pravila.application.use_cases.propose_rule_edit import (
    ProposeRuleEdit,
    ProposeRuleEditCommand,
)
from svoi_pravila.application.use_cases.reject_pending_rule import (
    RejectPendingRule,
    RejectPendingRuleCommand,
)
from svoi_pravila.application.use_cases.revoke_consent import RevokeConsent, RevokeConsentCommand
from svoi_pravila.domain.contact import MAX_CONTACTS_PER_USER, Contact
from svoi_pravila.domain.enums import ConsentKind, RelationshipKind, RuleCategory, RuleStatus
from svoi_pravila.domain.errors import InviteExpiredError
from svoi_pravila.domain.ids import ContactId, InviteId, PairId, RuleId
from svoi_pravila.domain.rules import MAX_OPEN_RULES_PER_SCOPE, RuleRevision
from svoi_pravila.domain.text import ContactLabel, RuleText
from svoi_pravila.domain.user import User
from tests.unit.application.conftest import AppWorld


async def _pair_world(
    world: AppWorld,
) -> tuple[User, User, Contact, AcceptInviteResult]:
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
async def test_private_and_shared_rules(world: AppWorld) -> None:
    inviter, invitee, contact, accepted = await _pair_world(world)

    private = await ProposeRule(
        world.uow_factory, world.catalog, world.ids, world.clock, world.notifier
    ).execute(
        ProposeRuleCommand(
            inviter.id,
            contact.id,
            RuleCategory.OTHER,
            RuleText("private note"),
            shared=False,
        )
    )
    assert private.rule.status is RuleStatus.ACTIVE

    shared = await ProposeRule(
        world.uow_factory, world.catalog, world.ids, world.clock, world.notifier
    ).execute(
        ProposeRuleCommand(
            inviter.id,
            contact.id,
            RuleCategory.TABOO_TOPIC,
            RuleText("shared taboo"),
            shared=True,
        )
    )
    assert shared.rule.status is RuleStatus.PROPOSED

    approved = await ApproveRule(
        world.uow_factory, world.catalog, world.clock, world.notifier
    ).execute(ApproveRuleCommand(invitee.id, shared.rule.id))
    assert approved.rule.status is RuleStatus.ACTIVE

    edited = await ProposeRuleEdit(world.uow_factory, world.catalog, world.clock).execute(
        ProposeRuleEditCommand(inviter.id, shared.rule.id, RuleText("shared taboo v2"))
    )
    assert edited.rule.effective_revision is not None
    assert edited.rule.effective_revision.text.value == "shared taboo"

    effective = await GetEffectiveRules(world.uow_factory, world.catalog).execute(
        GetEffectiveRulesCommand(invitee.id, accepted.invitee_contact.id)
    )
    texts = {r.text.value for r in effective.rules}
    assert "shared taboo" in texts
    assert "private note" not in texts

    listed = await ListRules(world.uow_factory, world.catalog).execute(
        ListRulesCommand(inviter.id, contact.id)
    )
    assert len(listed.rules) >= 2

    rejected_edit = await RejectPendingRule(
        world.uow_factory, world.catalog, world.clock, world.notifier
    ).execute(RejectPendingRuleCommand(invitee.id, shared.rule.id))
    assert rejected_edit.rule.pending_revision is None

    archived = await ArchiveRule(world.uow_factory, world.catalog, world.clock).execute(
        ArchiveRuleCommand(inviter.id, private.rule.id)
    )
    assert archived.rule.status is RuleStatus.ARCHIVED


@pytest.mark.unit
async def test_partner_cannot_list_or_get_rules_on_inviter_contact(world: AppWorld) -> None:
    inviter, invitee, contact, _accepted = await _pair_world(world)
    await ProposeRule(
        world.uow_factory, world.catalog, world.ids, world.clock, world.notifier
    ).execute(
        ProposeRuleCommand(
            inviter.id,
            contact.id,
            RuleCategory.OTHER,
            RuleText("secret private"),
            shared=False,
        )
    )
    with pytest.raises(NotFound):
        await ListRules(world.uow_factory, world.catalog).execute(
            ListRulesCommand(invitee.id, contact.id)
        )
    with pytest.raises(NotFound):
        await GetEffectiveRules(world.uow_factory, world.catalog).execute(
            GetEffectiveRulesCommand(invitee.id, contact.id)
        )


@pytest.mark.unit
async def test_accept_invite_result_excludes_inviter_contact_and_label(
    world: AppWorld,
) -> None:
    _inviter, _invitee, contact, accepted = await _pair_world(world)
    assert set(accepted.__dataclass_fields__) == {"pair", "invitee_contact"}
    assert accepted.invitee_contact.id != contact.id
    assert accepted.invitee_contact.label.value == "Inviter"
    assert contact.label.value == "Partner"


@pytest.mark.unit
async def test_stranger_not_found_for_rules_and_invites(world: AppWorld) -> None:
    inviter, _invitee, contact, accepted = await _pair_world(world)
    stranger = await world.ensure_granted_user(22)
    shared = await ProposeRule(
        world.uow_factory, world.catalog, world.ids, world.clock, world.notifier
    ).execute(
        ProposeRuleCommand(
            inviter.id,
            contact.id,
            RuleCategory.TABOO_TOPIC,
            RuleText("shared"),
            shared=True,
        )
    )
    with pytest.raises(NotFound):
        await ListRules(world.uow_factory, world.catalog).execute(
            ListRulesCommand(stranger.id, contact.id)
        )
    with pytest.raises(NotFound):
        await GetEffectiveRules(world.uow_factory, world.catalog).execute(
            GetEffectiveRulesCommand(stranger.id, accepted.invitee_contact.id)
        )
    with pytest.raises(NotFound):
        await ApproveRule(world.uow_factory, world.catalog, world.clock, world.notifier).execute(
            ApproveRuleCommand(stranger.id, shared.rule.id)
        )
    with pytest.raises(NotFound):
        await ProposeRule(
            world.uow_factory, world.catalog, world.ids, world.clock, world.notifier
        ).execute(
            ProposeRuleCommand(
                stranger.id,
                contact.id,
                RuleCategory.OTHER,
                RuleText("nope"),
                shared=False,
            )
        )
    with pytest.raises(NotFound):
        await CreateInvite(
            world.uow_factory, world.catalog, world.ids, world.tokens, world.clock
        ).execute(CreateInviteCommand(stranger.id, contact.id))


@pytest.mark.unit
async def test_effective_rules_sorted_by_effective_since_then_id(world: AppWorld) -> None:
    owner = await world.ensure_granted_user(23)
    contact = (
        await CreateContact(world.uow_factory, world.catalog, world.ids, world.clock).execute(
            CreateContactCommand(owner.id, ContactLabel("Solo"), RelationshipKind.OTHER)
        )
    ).contact
    first = await ProposeRule(
        world.uow_factory, world.catalog, world.ids, world.clock, world.notifier
    ).execute(
        ProposeRuleCommand(
            owner.id, contact.id, RuleCategory.OTHER, RuleText("earlier"), shared=False
        )
    )
    world.clock.advance(timedelta(seconds=5))
    second = await ProposeRule(
        world.uow_factory, world.catalog, world.ids, world.clock, world.notifier
    ).execute(
        ProposeRuleCommand(
            owner.id, contact.id, RuleCategory.OTHER, RuleText("later"), shared=False
        )
    )
    effective = await GetEffectiveRules(world.uow_factory, world.catalog).execute(
        GetEffectiveRulesCommand(owner.id, contact.id)
    )
    assert [r.rule_id for r in effective.rules] == [first.rule.id, second.rule.id]
    assert effective.rules[0].effective_since <= effective.rules[1].effective_since


@pytest.mark.unit
async def test_accept_invite_requires_inviter_access(world: AppWorld) -> None:
    inviter = await world.ensure_granted_user(30)
    invitee = await world.ensure_granted_user(31)
    contact = (
        await CreateContact(world.uow_factory, world.catalog, world.ids, world.clock).execute(
            CreateContactCommand(inviter.id, ContactLabel("C"), RelationshipKind.FRIEND)
        )
    ).contact
    invite = await CreateInvite(
        world.uow_factory, world.catalog, world.ids, world.tokens, world.clock
    ).execute(CreateInviteCommand(inviter.id, contact.id))
    await RevokeConsent(world.uow_factory, world.clock).execute(
        RevokeConsentCommand(inviter.id, ConsentKind.PERSONAL_DATA)
    )
    with pytest.raises(NotFound):
        await AcceptInvite(
            world.uow_factory, world.catalog, world.ids, world.clock, world.notifier
        ).execute(
            AcceptInviteCommand(
                invitee.id, invite.invite.id, ContactLabel("I"), RelationshipKind.FRIEND
            )
        )


@pytest.mark.unit
async def test_accept_invite_respects_invitee_contact_limit(world: AppWorld) -> None:
    inviter = await world.ensure_granted_user(32)
    invitee = await world.ensure_granted_user(33)
    contact = (
        await CreateContact(world.uow_factory, world.catalog, world.ids, world.clock).execute(
            CreateContactCommand(inviter.id, ContactLabel("C"), RelationshipKind.FRIEND)
        )
    ).contact
    invite = await CreateInvite(
        world.uow_factory, world.catalog, world.ids, world.tokens, world.clock
    ).execute(CreateInviteCommand(inviter.id, contact.id))
    create = CreateContact(world.uow_factory, world.catalog, world.ids, world.clock)
    for i in range(MAX_CONTACTS_PER_USER):
        await create.execute(
            CreateContactCommand(invitee.id, ContactLabel(f"x{i}"), RelationshipKind.OTHER)
        )
    with pytest.raises(ContactLimitReached):
        await AcceptInvite(
            world.uow_factory, world.catalog, world.ids, world.clock, world.notifier
        ).execute(
            AcceptInviteCommand(
                invitee.id, invite.invite.id, ContactLabel("I"), RelationshipKind.FRIEND
            )
        )


@pytest.mark.unit
async def test_invite_errors(world: AppWorld) -> None:
    inviter = await world.ensure_granted_user(40)
    invitee = await world.ensure_granted_user(41)
    other = await world.ensure_granted_user(42)
    contact = (
        await CreateContact(world.uow_factory, world.catalog, world.ids, world.clock).execute(
            CreateContactCommand(inviter.id, ContactLabel("C"), RelationshipKind.FRIEND)
        )
    ).contact

    with pytest.raises(NotFound):
        await CreateInvite(
            world.uow_factory, world.catalog, world.ids, world.tokens, world.clock
        ).execute(CreateInviteCommand(other.id, contact.id))

    invite = await CreateInvite(
        world.uow_factory, world.catalog, world.ids, world.tokens, world.clock
    ).execute(CreateInviteCommand(inviter.id, contact.id))

    await AcceptInvite(
        world.uow_factory, world.catalog, world.ids, world.clock, world.notifier
    ).execute(
        AcceptInviteCommand(
            invitee.id, invite.invite.id, ContactLabel("I"), RelationshipKind.FRIEND
        )
    )

    contact2 = (
        await CreateContact(world.uow_factory, world.catalog, world.ids, world.clock).execute(
            CreateContactCommand(inviter.id, ContactLabel("C2"), RelationshipKind.FRIEND)
        )
    ).contact
    invite2 = await CreateInvite(
        world.uow_factory, world.catalog, world.ids, world.tokens, world.clock
    ).execute(CreateInviteCommand(inviter.id, contact2.id))
    with pytest.raises(AlreadyPaired):
        await AcceptInvite(
            world.uow_factory, world.catalog, world.ids, world.clock, world.notifier
        ).execute(
            AcceptInviteCommand(
                invitee.id,
                invite2.invite.id,
                ContactLabel("I2"),
                RelationshipKind.FRIEND,
            )
        )

    with pytest.raises(NotFound):
        await AcceptInvite(
            world.uow_factory, world.catalog, world.ids, world.clock, world.notifier
        ).execute(
            AcceptInviteCommand(
                other.id,
                InviteId(UUID(int=999_999)),
                ContactLabel("X"),
                RelationshipKind.OTHER,
            )
        )

    contact3 = (
        await CreateContact(world.uow_factory, world.catalog, world.ids, world.clock).execute(
            CreateContactCommand(inviter.id, ContactLabel("C3"), RelationshipKind.OTHER)
        )
    ).contact
    invite3 = await CreateInvite(
        world.uow_factory, world.catalog, world.ids, world.tokens, world.clock
    ).execute(CreateInviteCommand(inviter.id, contact3.id))
    world.clock.advance(timedelta(days=8))
    with pytest.raises(InviteExpiredError):
        await AcceptInvite(
            world.uow_factory, world.catalog, world.ids, world.clock, world.notifier
        ).execute(
            AcceptInviteCommand(
                other.id, invite3.invite.id, ContactLabel("O"), RelationshipKind.OTHER
            )
        )


@pytest.mark.unit
async def test_shared_propose_requires_linked_contact(world: AppWorld) -> None:
    owner = await world.ensure_granted_user(50)
    contact = (
        await CreateContact(world.uow_factory, world.catalog, world.ids, world.clock).execute(
            CreateContactCommand(owner.id, ContactLabel("Solo"), RelationshipKind.OTHER)
        )
    ).contact
    with pytest.raises(NotFound):
        await ProposeRule(
            world.uow_factory, world.catalog, world.ids, world.clock, world.notifier
        ).execute(
            ProposeRuleCommand(
                owner.id,
                contact.id,
                RuleCategory.OTHER,
                RuleText("shared?"),
                shared=True,
            )
        )
    listed = await ListRules(world.uow_factory, world.catalog).execute(
        ListRulesCommand(owner.id, contact.id)
    )
    assert listed.rules == ()
    with pytest.raises(NotFound):
        await GetEffectiveRules(world.uow_factory, world.catalog).execute(
            GetEffectiveRulesCommand(owner.id, ContactId(UUID(int=99999)))
        )
    with pytest.raises(NotFound):
        await ArchiveRule(world.uow_factory, world.catalog, world.clock).execute(
            ArchiveRuleCommand(owner.id, RuleId(UUID(int=99999)))
        )
    with pytest.raises(NotFound):
        await ProposeRule(
            world.uow_factory, world.catalog, world.ids, world.clock, world.notifier
        ).execute(
            ProposeRuleCommand(
                owner.id,
                ContactId(UUID(int=99999)),
                RuleCategory.OTHER,
                RuleText("missing"),
                shared=False,
            )
        )


@pytest.mark.unit
async def test_create_invite_rejects_already_linked_contact(world: AppWorld) -> None:
    inviter, _invitee, contact, _accepted = await _pair_world(world)
    with pytest.raises(ContactAlreadyLinked):
        await CreateInvite(
            world.uow_factory, world.catalog, world.ids, world.tokens, world.clock
        ).execute(CreateInviteCommand(inviter.id, contact.id))


@pytest.mark.unit
async def test_accept_invite_rejects_reassigned_or_linked_contact(world: AppWorld) -> None:
    inviter = await world.ensure_granted_user(60)
    invitee = await world.ensure_granted_user(61)
    stranger = await world.ensure_granted_user(62)
    contact = (
        await CreateContact(world.uow_factory, world.catalog, world.ids, world.clock).execute(
            CreateContactCommand(inviter.id, ContactLabel("C"), RelationshipKind.FRIEND)
        )
    ).contact
    invite = await CreateInvite(
        world.uow_factory, world.catalog, world.ids, world.tokens, world.clock
    ).execute(CreateInviteCommand(inviter.id, contact.id))
    async with world.uow_factory() as uow:
        await uow.contacts.update(
            Contact(
                id=contact.id,
                owner_id=stranger.id,
                label=contact.label,
                relationship=contact.relationship,
                pair_id=None,
                created_at=contact.created_at,
            )
        )
        await uow.commit()
    with pytest.raises(NotFound):
        await AcceptInvite(
            world.uow_factory, world.catalog, world.ids, world.clock, world.notifier
        ).execute(
            AcceptInviteCommand(
                invitee.id, invite.invite.id, ContactLabel("I"), RelationshipKind.FRIEND
            )
        )

    contact2 = (
        await CreateContact(world.uow_factory, world.catalog, world.ids, world.clock).execute(
            CreateContactCommand(inviter.id, ContactLabel("C2"), RelationshipKind.FRIEND)
        )
    ).contact
    invite2 = await CreateInvite(
        world.uow_factory, world.catalog, world.ids, world.tokens, world.clock
    ).execute(CreateInviteCommand(inviter.id, contact2.id))
    async with world.uow_factory() as uow:
        linked = contact2.link_pair(PairId(UUID(int=777)))
        await uow.contacts.update(linked)
        await uow.commit()
    with pytest.raises(AlreadyPaired):
        await AcceptInvite(
            world.uow_factory, world.catalog, world.ids, world.clock, world.notifier
        ).execute(
            AcceptInviteCommand(
                invitee.id, invite2.invite.id, ContactLabel("I2"), RelationshipKind.FRIEND
            )
        )


@pytest.mark.unit
async def test_propose_shared_with_missing_pair_not_found(world: AppWorld) -> None:
    owner = await world.ensure_granted_user(70)
    contact = (
        await CreateContact(world.uow_factory, world.catalog, world.ids, world.clock).execute(
            CreateContactCommand(owner.id, ContactLabel("Solo"), RelationshipKind.OTHER)
        )
    ).contact
    async with world.uow_factory() as uow:
        await uow.contacts.update(contact.link_pair(PairId(UUID(int=888))))
        await uow.commit()
    with pytest.raises(NotFound):
        await ProposeRule(
            world.uow_factory, world.catalog, world.ids, world.clock, world.notifier
        ).execute(
            ProposeRuleCommand(
                owner.id,
                contact.id,
                RuleCategory.OTHER,
                RuleText("orphan pair"),
                shared=True,
            )
        )


@pytest.mark.unit
async def test_partner_cannot_propose_private_on_inviter_contact(world: AppWorld) -> None:
    _inviter, invitee, contact, _accepted = await _pair_world(world)
    with pytest.raises(NotFound):
        await ProposeRule(
            world.uow_factory, world.catalog, world.ids, world.clock, world.notifier
        ).execute(
            ProposeRuleCommand(
                invitee.id,
                contact.id,
                RuleCategory.OTHER,
                RuleText("not mine"),
                shared=False,
            )
        )


@pytest.mark.unit
async def test_open_rule_limit_reached(world: AppWorld) -> None:
    owner = await world.ensure_granted_user(80)
    contact = (
        await CreateContact(world.uow_factory, world.catalog, world.ids, world.clock).execute(
            CreateContactCommand(owner.id, ContactLabel("Solo"), RelationshipKind.OTHER)
        )
    ).contact
    propose = ProposeRule(world.uow_factory, world.catalog, world.ids, world.clock, world.notifier)
    for i in range(MAX_OPEN_RULES_PER_SCOPE):
        await propose.execute(
            ProposeRuleCommand(
                owner.id,
                contact.id,
                RuleCategory.OTHER,
                RuleText(f"rule {i}"),
                shared=False,
            )
        )
    with pytest.raises(OpenRuleLimitReached):
        await propose.execute(
            ProposeRuleCommand(
                owner.id,
                contact.id,
                RuleCategory.OTHER,
                RuleText("overflow"),
                shared=False,
            )
        )


@pytest.mark.unit
async def test_effective_rules_skip_non_active(world: AppWorld) -> None:
    inviter, invitee, contact, accepted = await _pair_world(world)
    await ProposeRule(
        world.uow_factory, world.catalog, world.ids, world.clock, world.notifier
    ).execute(
        ProposeRuleCommand(
            inviter.id,
            contact.id,
            RuleCategory.TABOO_TOPIC,
            RuleText("pending shared"),
            shared=True,
        )
    )
    effective = await GetEffectiveRules(world.uow_factory, world.catalog).execute(
        GetEffectiveRulesCommand(invitee.id, accepted.invitee_contact.id)
    )
    assert effective.rules == ()


@pytest.mark.unit
async def test_effective_rules_skip_active_without_effective_text(
    world: AppWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = await world.ensure_granted_user(90)
    contact = (
        await CreateContact(world.uow_factory, world.catalog, world.ids, world.clock).execute(
            CreateContactCommand(owner.id, ContactLabel("Solo"), RelationshipKind.OTHER)
        )
    ).contact
    await ProposeRule(
        world.uow_factory, world.catalog, world.ids, world.clock, world.notifier
    ).execute(
        ProposeRuleCommand(
            owner.id, contact.id, RuleCategory.OTHER, RuleText("active"), shared=False
        )
    )
    monkeypatch.setattr(
        "svoi_pravila.domain.rules.Rule.effective_revision",
        property(lambda self: None),
    )
    empty = await GetEffectiveRules(world.uow_factory, world.catalog).execute(
        GetEffectiveRulesCommand(owner.id, contact.id)
    )
    assert empty.rules == ()

    pending = RuleRevision(
        number=1,
        text=RuleText("pending shape"),
        author_id=owner.id,
        proposed_at=world.clock.now(),
        approved_by=frozenset({owner.id}),
        effective_since=None,
    )
    monkeypatch.setattr(
        "svoi_pravila.domain.rules.Rule.effective_revision",
        property(lambda self: pending),
    )
    still_empty = await GetEffectiveRules(world.uow_factory, world.catalog).execute(
        GetEffectiveRulesCommand(owner.id, contact.id)
    )
    assert still_empty.rules == ()


@pytest.mark.unit
async def test_resolve_invite_and_accept_by_id(world: AppWorld) -> None:
    from svoi_pravila.application.use_cases.resolve_invite import (
        ResolveInvite,
        ResolveInviteCommand,
    )
    from svoi_pravila.domain.errors import SelfInviteAcceptError

    inviter = await world.ensure_granted_user(60)
    invitee = await world.ensure_granted_user(61)
    contact = (
        await CreateContact(world.uow_factory, world.catalog, world.ids, world.clock).execute(
            CreateContactCommand(inviter.id, ContactLabel("Partner"), RelationshipKind.PARTNER)
        )
    ).contact
    created = await CreateInvite(
        world.uow_factory, world.catalog, world.ids, world.tokens, world.clock
    ).execute(CreateInviteCommand(inviter.id, contact.id))
    resolved = await ResolveInvite(world.uow_factory, world.catalog, world.clock).execute(
        ResolveInviteCommand(invitee.id, created.raw_token)
    )
    assert resolved.invite_id == created.invite.id
    with pytest.raises(SelfInviteAcceptError):
        await ResolveInvite(world.uow_factory, world.catalog, world.clock).execute(
            ResolveInviteCommand(inviter.id, created.raw_token)
        )
    accepted = await AcceptInvite(
        world.uow_factory, world.catalog, world.ids, world.clock, world.notifier
    ).execute(
        AcceptInviteCommand(
            invitee.id,
            resolved.invite_id,
            ContactLabel("Inviter"),
            RelationshipKind.PARTNER,
        )
    )
    assert accepted.invitee_contact.pair_id == accepted.pair.id
    assert world.notifier.invite_accepted_calls
    assert world.notifier.invite_accepted_calls[0].inviter_id == inviter.id
    assert world.notifier.invite_accepted_calls[0].inviter_contact_id == contact.id


@pytest.mark.unit
async def test_resolve_invite_expired_used_and_missing(world: AppWorld) -> None:
    from svoi_pravila.application.use_cases.resolve_invite import (
        ResolveInvite,
        ResolveInviteCommand,
    )
    from svoi_pravila.domain.errors import InviteAlreadyAcceptedError, InviteExpiredError
    from svoi_pravila.domain.invite import INVITE_TTL

    inviter = await world.ensure_granted_user(62)
    invitee = await world.ensure_granted_user(63)
    contact = (
        await CreateContact(world.uow_factory, world.catalog, world.ids, world.clock).execute(
            CreateContactCommand(inviter.id, ContactLabel("C"), RelationshipKind.FRIEND)
        )
    ).contact
    created = await CreateInvite(
        world.uow_factory, world.catalog, world.ids, world.tokens, world.clock
    ).execute(CreateInviteCommand(inviter.id, contact.id))
    with pytest.raises(NotFound):
        await ResolveInvite(world.uow_factory, world.catalog, world.clock).execute(
            ResolveInviteCommand(invitee.id, "missing-token-xxxxxxxxxxxx")
        )
    await AcceptInvite(
        world.uow_factory, world.catalog, world.ids, world.clock, world.notifier
    ).execute(
        AcceptInviteCommand(
            invitee.id, created.invite.id, ContactLabel("I"), RelationshipKind.FRIEND
        )
    )
    with pytest.raises(InviteAlreadyAcceptedError):
        await ResolveInvite(world.uow_factory, world.catalog, world.clock).execute(
            ResolveInviteCommand(invitee.id, created.raw_token)
        )

    contact2 = (
        await CreateContact(world.uow_factory, world.catalog, world.ids, world.clock).execute(
            CreateContactCommand(inviter.id, ContactLabel("C2"), RelationshipKind.FRIEND)
        )
    ).contact
    created2 = await CreateInvite(
        world.uow_factory, world.catalog, world.ids, world.tokens, world.clock
    ).execute(CreateInviteCommand(inviter.id, contact2.id))
    world.clock.advance(INVITE_TTL)
    with pytest.raises(InviteExpiredError):
        await ResolveInvite(world.uow_factory, world.catalog, world.clock).execute(
            ResolveInviteCommand(invitee.id, created2.raw_token)
        )


@pytest.mark.unit
async def test_create_invite_revokes_previous_pending(world: AppWorld) -> None:
    from svoi_pravila.application.use_cases.resolve_invite import (
        ResolveInvite,
        ResolveInviteCommand,
    )

    inviter = await world.ensure_granted_user(64)
    invitee = await world.ensure_granted_user(65)
    contact = (
        await CreateContact(world.uow_factory, world.catalog, world.ids, world.clock).execute(
            CreateContactCommand(inviter.id, ContactLabel("C"), RelationshipKind.FRIEND)
        )
    ).contact
    first = await CreateInvite(
        world.uow_factory, world.catalog, world.ids, world.tokens, world.clock
    ).execute(CreateInviteCommand(inviter.id, contact.id))
    second = await CreateInvite(
        world.uow_factory, world.catalog, world.ids, world.tokens, world.clock
    ).execute(CreateInviteCommand(inviter.id, contact.id))
    assert first.invite.id != second.invite.id
    with pytest.raises(NotFound):
        await ResolveInvite(world.uow_factory, world.catalog, world.clock).execute(
            ResolveInviteCommand(invitee.id, first.raw_token)
        )
    resolved = await ResolveInvite(world.uow_factory, world.catalog, world.clock).execute(
        ResolveInviteCommand(invitee.id, second.raw_token)
    )
    assert resolved.invite_id == second.invite.id


@pytest.mark.unit
async def test_notifier_after_commit_and_failure_isolated(world: AppWorld) -> None:
    from tests.fakes.pair_notifier import FakePairNotifier

    inviter, invitee, contact, accepted = await _pair_world(world)
    assert world.notifier.invite_accepted_calls
    failing = FakePairNotifier(fail=True)
    shared = await ProposeRule(
        world.uow_factory, world.catalog, world.ids, world.clock, failing
    ).execute(
        ProposeRuleCommand(
            inviter.id,
            contact.id,
            RuleCategory.TABOO_TOPIC,
            RuleText("shared notify"),
            shared=True,
        )
    )
    assert shared.rule.status is RuleStatus.PROPOSED
    assert failing.shared_rule_proposed_calls
    await ApproveRule(world.uow_factory, world.catalog, world.clock, failing).execute(
        ApproveRuleCommand(invitee.id, shared.rule.id)
    )
    assert failing.shared_rule_decided_calls
    assert failing.shared_rule_decided_calls[0].approved is True
    leave_notifier = FakePairNotifier(fail=True)
    await LeavePair(world.uow_factory, world.ids, world.clock, leave_notifier).execute(
        LeavePairCommand(inviter.id, accepted.pair.id)
    )
    assert leave_notifier.partner_left_calls
    async with world.uow_factory() as uow:
        assert await uow.pairs.get(accepted.pair.id) is None


@pytest.mark.unit
async def test_resolve_invite_requires_inviter_access(world: AppWorld) -> None:
    from svoi_pravila.application.use_cases.resolve_invite import (
        ResolveInvite,
        ResolveInviteCommand,
    )

    inviter = await world.ensure_granted_user(66)
    invitee = await world.ensure_granted_user(67)
    contact = (
        await CreateContact(world.uow_factory, world.catalog, world.ids, world.clock).execute(
            CreateContactCommand(inviter.id, ContactLabel("C"), RelationshipKind.FRIEND)
        )
    ).contact
    created = await CreateInvite(
        world.uow_factory, world.catalog, world.ids, world.tokens, world.clock
    ).execute(CreateInviteCommand(inviter.id, contact.id))
    await RevokeConsent(world.uow_factory, world.clock).execute(
        RevokeConsentCommand(inviter.id, ConsentKind.PERSONAL_DATA)
    )
    with pytest.raises(NotFound):
        await ResolveInvite(world.uow_factory, world.catalog, world.clock).execute(
            ResolveInviteCommand(invitee.id, created.raw_token)
        )


@pytest.mark.unit
async def test_accept_invite_keeps_existing_active_contact(world: AppWorld) -> None:
    inviter = await world.ensure_granted_user(68)
    invitee = await world.ensure_granted_user(69)
    existing = (
        await CreateContact(world.uow_factory, world.catalog, world.ids, world.clock).execute(
            CreateContactCommand(invitee.id, ContactLabel("Prior"), RelationshipKind.FRIEND)
        )
    ).contact
    contact = (
        await CreateContact(world.uow_factory, world.catalog, world.ids, world.clock).execute(
            CreateContactCommand(inviter.id, ContactLabel("C"), RelationshipKind.FRIEND)
        )
    ).contact
    created = await CreateInvite(
        world.uow_factory, world.catalog, world.ids, world.tokens, world.clock
    ).execute(CreateInviteCommand(inviter.id, contact.id))
    accepted = await AcceptInvite(
        world.uow_factory, world.catalog, world.ids, world.clock, world.notifier
    ).execute(
        AcceptInviteCommand(
            invitee.id, created.invite.id, ContactLabel("I"), RelationshipKind.FRIEND
        )
    )
    async with world.uow_factory() as uow:
        invitee_user = await uow.users.get(invitee.id)
        assert invitee_user is not None
        assert invitee_user.active_contact_id == existing.id
        assert accepted.invitee_contact.id != existing.id


@pytest.mark.unit
async def test_accept_invite_missing_user_after_mutations(world: AppWorld) -> None:
    from tests.unit.application.test_contacts import _HideUserFactory

    inviter = await world.ensure_granted_user(74)
    invitee = await world.ensure_granted_user(75)
    contact = (
        await CreateContact(world.uow_factory, world.catalog, world.ids, world.clock).execute(
            CreateContactCommand(inviter.id, ContactLabel("C"), RelationshipKind.FRIEND)
        )
    ).contact
    created = await CreateInvite(
        world.uow_factory, world.catalog, world.ids, world.tokens, world.clock
    ).execute(CreateInviteCommand(inviter.id, contact.id))
    hiding = _HideUserFactory(world.uow_factory, invitee.id)
    with pytest.raises(NotFound):
        await AcceptInvite(hiding, world.catalog, world.ids, world.clock, world.notifier).execute(
            AcceptInviteCommand(
                invitee.id, created.invite.id, ContactLabel("I"), RelationshipKind.FRIEND
            )
        )


@pytest.mark.unit
async def test_approve_and_reject_require_pending_revision(world: AppWorld) -> None:
    inviter, invitee, contact, _accepted = await _pair_world(world)
    private = await ProposeRule(
        world.uow_factory, world.catalog, world.ids, world.clock, world.notifier
    ).execute(
        ProposeRuleCommand(
            inviter.id,
            contact.id,
            RuleCategory.OTHER,
            RuleText("no pending"),
            shared=False,
        )
    )
    with pytest.raises(NotFound):
        await ApproveRule(world.uow_factory, world.catalog, world.clock, world.notifier).execute(
            ApproveRuleCommand(inviter.id, private.rule.id)
        )
    shared = await ProposeRule(
        world.uow_factory, world.catalog, world.ids, world.clock, world.notifier
    ).execute(
        ProposeRuleCommand(
            inviter.id,
            contact.id,
            RuleCategory.OTHER,
            RuleText("to approve first"),
            shared=True,
        )
    )
    await ApproveRule(world.uow_factory, world.catalog, world.clock, world.notifier).execute(
        ApproveRuleCommand(invitee.id, shared.rule.id)
    )
    with pytest.raises(NotFound):
        await RejectPendingRule(
            world.uow_factory, world.catalog, world.clock, world.notifier
        ).execute(RejectPendingRuleCommand(invitee.id, shared.rule.id))
