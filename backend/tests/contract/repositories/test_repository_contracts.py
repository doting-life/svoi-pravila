"""Repository behaviour suite against ports (in-memory now; Postgres in 0003)."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest

from svoi_pravila.application.errors import ConflictError
from svoi_pravila.application.ports.unit_of_work import UnitOfWork, UnitOfWorkFactory
from svoi_pravila.domain.consent import Consent
from svoi_pravila.domain.contact import Contact
from svoi_pravila.domain.enums import (
    ConsentKind,
    RelationshipKind,
    RuleCategory,
    RuleStatus,
    UsageOutcome,
    UsageScenario,
    UsageSurface,
)
from svoi_pravila.domain.ids import (
    ConsentId,
    ContactId,
    InviteId,
    PairId,
    RuleId,
    TelegramUserId,
    UsageEventId,
    UserId,
)
from svoi_pravila.domain.invite import Invite, InviteTokenHash
from svoi_pravila.domain.pair import Pair
from svoi_pravila.domain.rules import ContactScope, Rule
from svoi_pravila.domain.text import ContactLabel, RuleText, Sha256Hex
from svoi_pravila.domain.usage import UsageEvent
from svoi_pravila.domain.user import User

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _user(n: int, telegram: int) -> User:
    return User(
        id=UserId(UUID(int=n)),
        telegram_user_id=TelegramUserId(telegram),
        created_at=NOW,
        age_confirmed_at=None,
        active_contact_id=None,
    )


async def _seed_users(uow: UnitOfWork, *users: User) -> None:
    for user in users:
        await uow.users.add(user)


@pytest.mark.unit
async def test_user_repo_roundtrip(uow_factory: UnitOfWorkFactory) -> None:
    user = _user(1, 42)
    async with uow_factory() as uow:
        await uow.users.add(user)
        assert await uow.users.get(user.id) == user
    async with uow_factory() as uow:
        assert await uow.users.get(user.id) is None

    async with uow_factory() as uow:
        await uow.users.add(user)
        await uow.commit()
    async with uow_factory() as uow:
        loaded = await uow.users.get_by_telegram_id(TelegramUserId(42))
        assert loaded is not None
        assert loaded.id == user.id
        updated = loaded.confirm_age(NOW)
        await uow.users.update(updated)
        await uow.commit()
    async with uow_factory() as uow:
        confirmed = await uow.users.get(user.id)
        assert confirmed is not None
        assert confirmed.age_confirmed_at == NOW


@pytest.mark.unit
async def test_user_add_duplicate_telegram_id_conflicts(uow_factory: UnitOfWorkFactory) -> None:
    first = _user(1, 42)
    second = _user(2, 42)
    async with uow_factory() as uow:
        await uow.users.add(first)
        await uow.commit()
    async with uow_factory() as uow:
        with pytest.raises(ConflictError):
            await uow.users.add(second)


@pytest.mark.unit
async def test_invite_add_duplicate_token_hash_conflicts(uow_factory: UnitOfWorkFactory) -> None:
    token_hash = InviteTokenHash.from_raw_token("same-token")
    inviter_a = _user(10, 110)
    inviter_b = _user(11, 111)
    contact_a = Contact(
        id=ContactId(UUID(int=20)),
        owner_id=inviter_a.id,
        label=ContactLabel("A"),
        relationship=RelationshipKind.FRIEND,
        pair_id=None,
        created_at=NOW,
    )
    contact_b = Contact(
        id=ContactId(UUID(int=21)),
        owner_id=inviter_b.id,
        label=ContactLabel("B"),
        relationship=RelationshipKind.FRIEND,
        pair_id=None,
        created_at=NOW,
    )
    first = Invite.create(
        invite_id=InviteId(UUID(int=50)),
        inviter_id=inviter_a.id,
        contact_id=contact_a.id,
        token_hash=token_hash,
        created_at=NOW,
    )
    second = Invite.create(
        invite_id=InviteId(UUID(int=51)),
        inviter_id=inviter_b.id,
        contact_id=contact_b.id,
        token_hash=token_hash,
        created_at=NOW,
    )
    async with uow_factory() as uow:
        await _seed_users(uow, inviter_a, inviter_b)
        await uow.contacts.add(contact_a)
        await uow.contacts.add(contact_b)
        await uow.invites.add(first)
        await uow.commit()
    async with uow_factory() as uow:
        with pytest.raises(ConflictError):
            await uow.invites.add(second)


@pytest.mark.unit
async def test_pair_add_duplicate_members_conflicts(uow_factory: UnitOfWorkFactory) -> None:
    a_user = _user(10, 210)
    b_user = _user(11, 211)
    a, b = a_user.id, b_user.id
    first = Pair(id=PairId(UUID(int=30)), members=frozenset({a, b}), created_at=NOW)
    second = Pair(id=PairId(UUID(int=31)), members=frozenset({a, b}), created_at=NOW)
    async with uow_factory() as uow:
        await _seed_users(uow, a_user, b_user)
        await uow.pairs.add(first)
        await uow.commit()
    async with uow_factory() as uow:
        with pytest.raises(ConflictError):
            await uow.pairs.add(second)


@pytest.mark.unit
async def test_contact_pair_rule_invite_repos(uow_factory: UnitOfWorkFactory) -> None:
    owner_user = _user(10, 310)
    partner_user = _user(11, 311)
    owner = owner_user.id
    partner = partner_user.id
    contact = Contact(
        id=ContactId(UUID(int=20)),
        owner_id=owner,
        label=ContactLabel("A"),
        relationship=RelationshipKind.FRIEND,
        pair_id=None,
        created_at=NOW,
    )
    pair = Pair(
        id=PairId(UUID(int=30)),
        members=frozenset({owner, partner}),
        created_at=NOW,
    )
    rule = Rule.propose(
        rule_id=RuleId(UUID(int=40)),
        scope=ContactScope(contact_id=contact.id),
        category=RuleCategory.OTHER,
        approvers=frozenset({owner}),
        author_id=owner,
        text=RuleText("hello"),
        now=NOW,
    )
    invite = Invite.create(
        invite_id=InviteId(UUID(int=50)),
        inviter_id=owner,
        contact_id=contact.id,
        token_hash=InviteTokenHash.from_raw_token("contract-token"),
        created_at=NOW,
    )
    consent = Consent(
        id=ConsentId(UUID(int=60)),
        user_id=owner,
        kind=ConsentKind.PERSONAL_DATA,
        text_version="v1",
        text_sha256=Sha256Hex("d" * 64),
        granted_at=NOW,
        revoked_at=None,
    )

    async with uow_factory() as uow:
        await _seed_users(uow, owner_user, partner_user)
        await uow.contacts.add(contact)
        await uow.pairs.add(pair)
        await uow.rules.add(rule)
        await uow.invites.add(invite)
        await uow.consents.add(consent)
        await uow.commit()

    async with uow_factory() as uow:
        assert await uow.contacts.count_for_owner(owner) == 1
        assert len(await uow.contacts.list_for_owner(owner)) == 1
        found_pair = await uow.pairs.find_between(owner, partner)
        assert found_pair is not None
        assert found_pair.id == pair.id
        assert await uow.rules.count_open_for_scope(ContactScope(contact_id=contact.id)) == 1
        listed = await uow.rules.list_for_scope(ContactScope(contact_id=contact.id))
        assert listed[0].status is RuleStatus.ACTIVE
        by_hash = await uow.invites.get_by_token_hash(invite.token_hash)
        assert by_hash is not None
        consents = await uow.consents.list_for_user(owner)
        assert len(consents) == 1
        revoked = consents[0].revoke(NOW)
        await uow.consents.update(revoked)
        linked = contact.link_pair(pair.id)
        await uow.contacts.update(linked)
        archived = rule.archive(owner, NOW)
        await uow.rules.update(archived)
        accepted = invite.accept(partner, NOW)
        await uow.invites.update(accepted)
        await uow.commit()

    async with uow_factory() as uow:
        stored_contact = await uow.contacts.get(contact.id)
        assert stored_contact is not None
        assert stored_contact.pair_id == pair.id
        stored_rule = await uow.rules.get(rule.id)
        assert stored_rule is not None
        assert stored_rule.status is RuleStatus.ARCHIVED
        stored_invite = await uow.invites.get(invite.id)
        assert stored_invite is not None
        assert stored_invite.accepted_by == partner
        assert (await uow.consents.list_for_user(owner))[0].revoked_at == NOW


@pytest.mark.unit
async def test_repository_deletes(uow_factory: UnitOfWorkFactory) -> None:
    owner_user = _user(12, 410)
    partner_user = _user(13, 411)
    owner = owner_user.id
    partner = partner_user.id
    contact = Contact(
        id=ContactId(UUID(int=22)),
        owner_id=owner,
        label=ContactLabel("A"),
        relationship=RelationshipKind.FRIEND,
        pair_id=None,
        created_at=NOW,
    )
    pair = Pair(
        id=PairId(UUID(int=32)),
        members=frozenset({owner, partner}),
        created_at=NOW,
    )
    rule = Rule.propose(
        rule_id=RuleId(UUID(int=42)),
        scope=ContactScope(contact_id=contact.id),
        category=RuleCategory.OTHER,
        approvers=frozenset({owner}),
        author_id=owner,
        text=RuleText("hello"),
        now=NOW,
    )
    invite = Invite.create(
        invite_id=InviteId(UUID(int=52)),
        inviter_id=owner,
        contact_id=contact.id,
        token_hash=InviteTokenHash.from_raw_token("delete-token"),
        created_at=NOW,
    )
    consent = Consent(
        id=ConsentId(UUID(int=62)),
        user_id=owner,
        kind=ConsentKind.PERSONAL_DATA,
        text_version="v1",
        text_sha256=Sha256Hex("d" * 64),
        granted_at=NOW,
        revoked_at=None,
    )
    async with uow_factory() as uow:
        await _seed_users(uow, owner_user, partner_user)
        await uow.contacts.add(contact)
        await uow.pairs.add(pair)
        linked = contact.link_pair(pair.id)
        await uow.contacts.update(linked)
        await uow.rules.add(rule)
        await uow.invites.add(invite)
        await uow.consents.add(consent)
        assert await uow.contacts.get_for_owner_and_pair(owner, pair.id) is not None
        assert await uow.invites.list_involving(owner)
        assert await uow.pairs.list_for_member(owner)
        await uow.invites.delete(invite.id)
        await uow.rules.delete(rule.id)
        await uow.contacts.delete(contact.id)
        await uow.pairs.delete(pair.id)
        await uow.consents.delete_for_user(owner)
        await uow.users.delete(owner)
        await uow.commit()
    async with uow_factory() as uow:
        assert await uow.users.get(owner) is None
        assert await uow.rules.get(rule.id) is None
        assert await uow.contacts.get(contact.id) is None
        assert await uow.pairs.get(pair.id) is None
        assert await uow.invites.get(invite.id) is None


@pytest.mark.unit
async def test_usage_event_repo_roundtrip(uow_factory: UnitOfWorkFactory) -> None:
    event = UsageEvent(
        id=UsageEventId(UUID(int=70)),
        occurred_at=NOW,
        user_pseudonym="ab" * 32,
        scenario=UsageScenario.DECODE,
        surface=UsageSurface.DM,
        outcome=UsageOutcome.OK,
        unavailable_kind=None,
        safety="ok",
        model="fake",
        prompt_version="decode@v1",
        latency_ms=12,
        ttfc_ms=3,
        attempts=1,
        input_tokens=4,
        output_tokens=5,
        billable_tokens=9,
    )
    async with uow_factory() as uow:
        await uow.usage_events.add(event)
        await uow.commit()
    async with uow_factory() as uow:
        loaded = await uow.usage_events.get(event.id)
        assert loaded == event
        missing = await uow.usage_events.get(UsageEventId(UUID(int=71)))
        assert missing is None
        await uow.usage_events.delete_for_pseudonym(event.user_pseudonym)
        await uow.commit()
    async with uow_factory() as uow:
        assert await uow.usage_events.get(event.id) is None
