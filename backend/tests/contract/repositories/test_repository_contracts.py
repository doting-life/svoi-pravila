"""Repository behaviour suite against ports (in-memory now; Postgres in 0003)."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest

from svoi_pravila.application.errors import ConflictError
from svoi_pravila.application.ports.unit_of_work import UnitOfWorkFactory
from svoi_pravila.domain.consent import Consent
from svoi_pravila.domain.contact import Contact
from svoi_pravila.domain.enums import ConsentKind, RelationshipKind, RuleCategory, RuleStatus
from svoi_pravila.domain.ids import (
    ConsentId,
    ContactId,
    InviteId,
    PairId,
    RuleId,
    TelegramUserId,
    UserId,
)
from svoi_pravila.domain.invite import Invite, InviteTokenHash
from svoi_pravila.domain.pair import Pair
from svoi_pravila.domain.rules import ContactScope, Rule
from svoi_pravila.domain.text import ContactLabel, RuleText, Sha256Hex
from svoi_pravila.domain.user import User

NOW = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.mark.unit
async def test_user_repo_roundtrip(uow_factory: UnitOfWorkFactory) -> None:
    user = User(
        id=UserId(UUID(int=1)),
        telegram_user_id=TelegramUserId(42),
        created_at=NOW,
        age_confirmed_at=None,
        active_contact_id=None,
    )
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
    first = User(
        id=UserId(UUID(int=1)),
        telegram_user_id=TelegramUserId(42),
        created_at=NOW,
        age_confirmed_at=None,
        active_contact_id=None,
    )
    second = User(
        id=UserId(UUID(int=2)),
        telegram_user_id=TelegramUserId(42),
        created_at=NOW,
        age_confirmed_at=None,
        active_contact_id=None,
    )
    async with uow_factory() as uow:
        await uow.users.add(first)
        await uow.commit()
    async with uow_factory() as uow:
        with pytest.raises(ConflictError):
            await uow.users.add(second)


@pytest.mark.unit
async def test_invite_add_duplicate_token_hash_conflicts(uow_factory: UnitOfWorkFactory) -> None:
    token_hash = InviteTokenHash.from_raw_token("same-token")
    first = Invite.create(
        invite_id=InviteId(UUID(int=50)),
        inviter_id=UserId(UUID(int=10)),
        contact_id=ContactId(UUID(int=20)),
        token_hash=token_hash,
        created_at=NOW,
    )
    second = Invite.create(
        invite_id=InviteId(UUID(int=51)),
        inviter_id=UserId(UUID(int=11)),
        contact_id=ContactId(UUID(int=21)),
        token_hash=token_hash,
        created_at=NOW,
    )
    async with uow_factory() as uow:
        await uow.invites.add(first)
        await uow.commit()
    async with uow_factory() as uow:
        with pytest.raises(ConflictError):
            await uow.invites.add(second)


@pytest.mark.unit
async def test_pair_add_duplicate_members_conflicts(uow_factory: UnitOfWorkFactory) -> None:
    a, b = UserId(UUID(int=10)), UserId(UUID(int=11))
    first = Pair(id=PairId(UUID(int=30)), members=frozenset({a, b}), created_at=NOW)
    second = Pair(id=PairId(UUID(int=31)), members=frozenset({a, b}), created_at=NOW)
    async with uow_factory() as uow:
        await uow.pairs.add(first)
        await uow.commit()
    async with uow_factory() as uow:
        with pytest.raises(ConflictError):
            await uow.pairs.add(second)


@pytest.mark.unit
async def test_contact_pair_rule_invite_repos(uow_factory: UnitOfWorkFactory) -> None:
    owner = UserId(UUID(int=10))
    partner = UserId(UUID(int=11))
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
