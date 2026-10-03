"""Conflict translation, OCC stale updates, and non-conflict integrity errors."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from svoi_pravila.adapters.persistence.errors import DataKeyUnavailableError, RowNotLoadedError
from svoi_pravila.adapters.persistence.uow import SqlAlchemyUnitOfWorkFactory
from svoi_pravila.application.errors import ConflictError
from svoi_pravila.domain.contact import Contact
from svoi_pravila.domain.enums import RelationshipKind, RuleCategory
from svoi_pravila.domain.ids import ContactId, InviteId, PairId, RuleId, TelegramUserId, UserId
from svoi_pravila.domain.invite import Invite, InviteTokenHash
from svoi_pravila.domain.pair import Pair
from svoi_pravila.domain.rules import ContactScope, PairScope, Rule
from svoi_pravila.domain.text import ContactLabel, RuleText
from svoi_pravila.domain.user import User

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _user(n: int, telegram: int | None = None, *, age_confirmed: bool = True) -> User:
    return User(
        id=UserId(UUID(int=n)),
        telegram_user_id=TelegramUserId(telegram if telegram is not None else 2000 + n),
        created_at=NOW,
        age_confirmed_at=NOW if age_confirmed else None,
        active_contact_id=None,
    )


@pytest.mark.integration
async def test_add_duplicate_telegram_id_conflicts(
    uow_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    async with uow_factory() as uow:
        await uow.users.add(_user(1, 42))
        await uow.commit()
    async with uow_factory() as uow:
        with pytest.raises(ConflictError):
            await uow.users.add(_user(2, 42))


@pytest.mark.integration
async def test_add_duplicate_invite_token_hash_conflicts(
    uow_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    a = _user(10)
    b = _user(11)
    ca = Contact(
        id=ContactId(UUID(int=20)),
        owner_id=a.id,
        label=ContactLabel("A"),
        relationship=RelationshipKind.FRIEND,
        pair_id=None,
        created_at=NOW,
    )
    cb = Contact(
        id=ContactId(UUID(int=21)),
        owner_id=b.id,
        label=ContactLabel("B"),
        relationship=RelationshipKind.FRIEND,
        pair_id=None,
        created_at=NOW,
    )
    token = InviteTokenHash.from_raw_token("dup-token")
    first = Invite.create(
        invite_id=InviteId(UUID(int=50)),
        inviter_id=a.id,
        contact_id=ca.id,
        token_hash=token,
        created_at=NOW,
    )
    second = Invite.create(
        invite_id=InviteId(UUID(int=51)),
        inviter_id=b.id,
        contact_id=cb.id,
        token_hash=token,
        created_at=NOW,
    )
    async with uow_factory() as uow:
        await uow.users.add(a)
        await uow.users.add(b)
        await uow.contacts.add(ca)
        await uow.contacts.add(cb)
        await uow.invites.add(first)
        await uow.commit()
    async with uow_factory() as uow:
        with pytest.raises(ConflictError):
            await uow.invites.add(second)


@pytest.mark.integration
async def test_add_duplicate_pair_members_conflicts(
    uow_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    a = _user(12)
    b = _user(13)
    first = Pair(id=PairId(UUID(int=30)), members=frozenset({a.id, b.id}), created_at=NOW)
    second = Pair(id=PairId(UUID(int=31)), members=frozenset({a.id, b.id}), created_at=NOW)
    async with uow_factory() as uow:
        await uow.users.add(a)
        await uow.users.add(b)
        await uow.pairs.add(first)
        await uow.commit()
    async with uow_factory() as uow:
        with pytest.raises(ConflictError):
            await uow.pairs.add(second)


@pytest.mark.integration
async def test_fk_violation_propagates_unchanged(
    uow_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    owner = _user(98)
    dangling_pair = Contact(
        id=ContactId(UUID(int=99)),
        owner_id=owner.id,
        label=ContactLabel("Dangling"),
        relationship=RelationshipKind.OTHER,
        pair_id=PairId(UUID(int=97)),
        created_at=NOW,
    )
    async with uow_factory() as uow:
        await uow.users.add(owner)
        with pytest.raises(IntegrityError):
            await uow.contacts.add(dangling_pair)


@pytest.mark.integration
async def test_update_without_load_raises_row_not_loaded(
    uow_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    user = _user(70)
    async with uow_factory() as uow:
        await uow.users.add(user)
        await uow.commit()
    async with uow_factory() as uow:
        with pytest.raises(RowNotLoadedError):
            await uow.users.update(user.confirm_age(NOW))


@pytest.mark.integration
async def test_stale_user_update_conflicts(
    uow_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    user = _user(71, age_confirmed=False)
    async with uow_factory() as uow:
        await uow.users.add(user)
        await uow.commit()
    async with uow_factory() as uow1:
        loaded1 = await uow1.users.get(user.id)
        assert loaded1 is not None
        async with uow_factory() as uow2:
            loaded2 = await uow2.users.get(user.id)
            assert loaded2 is not None
            await uow2.users.update(loaded2.confirm_age(NOW))
            await uow2.commit()
        with pytest.raises(ConflictError):
            await uow1.users.update(loaded1.confirm_age(NOW + timedelta(seconds=1)))
            await uow1.commit()


@pytest.mark.integration
async def test_stale_contact_update_conflicts(
    uow_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    owner = _user(72)
    contact = Contact(
        id=ContactId(UUID(int=73)),
        owner_id=owner.id,
        label=ContactLabel("C"),
        relationship=RelationshipKind.FRIEND,
        pair_id=None,
        created_at=NOW,
    )
    async with uow_factory() as uow:
        await uow.users.add(owner)
        await uow.contacts.add(contact)
        await uow.commit()
    async with uow_factory() as uow1:
        c1 = await uow1.contacts.get(contact.id)
        assert c1 is not None
        async with uow_factory() as uow2:
            c2 = await uow2.contacts.get(contact.id)
            assert c2 is not None
            await uow2.contacts.update(c2.rename(ContactLabel("Renamed")))
            await uow2.commit()
        with pytest.raises(ConflictError):
            await uow1.contacts.update(c1.rename(ContactLabel("Other")))
            await uow1.commit()


@pytest.mark.integration
async def test_stale_rule_update_conflicts(
    uow_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    owner = _user(74)
    contact = Contact(
        id=ContactId(UUID(int=75)),
        owner_id=owner.id,
        label=ContactLabel("R"),
        relationship=RelationshipKind.OTHER,
        pair_id=None,
        created_at=NOW,
    )
    rule = Rule.propose(
        rule_id=RuleId(UUID(int=76)),
        scope=ContactScope(contact_id=contact.id),
        category=RuleCategory.OTHER,
        approvers=frozenset({owner.id}),
        author_id=owner.id,
        text=RuleText("r"),
        now=NOW,
    )
    async with uow_factory() as uow:
        await uow.users.add(owner)
        await uow.contacts.add(contact)
        await uow.rules.add(rule)
        await uow.commit()
    async with uow_factory() as uow1:
        r1 = await uow1.rules.get(rule.id)
        assert r1 is not None
        async with uow_factory() as uow2:
            r2 = await uow2.rules.get(rule.id)
            assert r2 is not None
            await uow2.rules.update(r2.archive(owner.id, NOW))
            await uow2.commit()
        with pytest.raises(ConflictError):
            await uow1.rules.update(r1.archive(owner.id, NOW + timedelta(seconds=1)))
            await uow1.commit()


@pytest.mark.integration
async def test_stale_invite_update_conflicts(
    uow_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    inviter = _user(77)
    invitee_a = _user(78)
    invitee_b = _user(79)
    contact = Contact(
        id=ContactId(UUID(int=80)),
        owner_id=inviter.id,
        label=ContactLabel("I"),
        relationship=RelationshipKind.FRIEND,
        pair_id=None,
        created_at=NOW,
    )
    invite = Invite.create(
        invite_id=InviteId(UUID(int=81)),
        inviter_id=inviter.id,
        contact_id=contact.id,
        token_hash=InviteTokenHash.from_raw_token("stale-invite"),
        created_at=NOW,
    )
    async with uow_factory() as uow:
        await uow.users.add(inviter)
        await uow.users.add(invitee_a)
        await uow.users.add(invitee_b)
        await uow.contacts.add(contact)
        await uow.invites.add(invite)
        await uow.commit()
    async with uow_factory() as uow1:
        i1 = await uow1.invites.get(invite.id)
        assert i1 is not None
        async with uow_factory() as uow2:
            i2 = await uow2.invites.get(invite.id)
            assert i2 is not None
            await uow2.invites.update(i2.accept(invitee_b.id, NOW))
            await uow2.commit()
        with pytest.raises(ConflictError):
            await uow1.invites.update(i1.accept(invitee_a.id, NOW))
            await uow1.commit()


@pytest.mark.integration
async def test_missing_gets_return_none(
    uow_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    missing_user = UserId(UUID(int=900))
    missing_contact = ContactId(UUID(int=901))
    missing_pair = PairId(UUID(int=902))
    missing_rule = RuleId(UUID(int=903))
    missing_invite = InviteId(UUID(int=904))
    async with uow_factory() as uow:
        assert await uow.users.get(missing_user) is None
        assert await uow.users.get_by_telegram_id(TelegramUserId(999_001)) is None
        assert await uow.contacts.get(missing_contact) is None
        assert await uow.pairs.get(missing_pair) is None
        assert await uow.pairs.find_between(missing_user, UserId(UUID(int=905))) is None
        assert await uow.rules.get(missing_rule) is None
        assert await uow.invites.get(missing_invite) is None
        assert await uow.invites.get_by_token_hash(InviteTokenHash.from_raw_token("absent")) is None


@pytest.mark.integration
async def test_pair_scope_list_for_scope(
    uow_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    a = _user(910)
    b = _user(911)
    pair = Pair(id=PairId(UUID(int=912)), members=frozenset({a.id, b.id}), created_at=NOW)
    rule = Rule.propose(
        rule_id=RuleId(UUID(int=913)),
        scope=PairScope(pair_id=pair.id),
        category=RuleCategory.OTHER,
        approvers=frozenset({a.id, b.id}),
        author_id=a.id,
        text=RuleText("pair-rule"),
        now=NOW,
    )
    async with uow_factory() as uow:
        await uow.users.add(a)
        await uow.users.add(b)
        await uow.pairs.add(pair)
        await uow.rules.add(rule)
        await uow.commit()
    async with uow_factory() as uow:
        loaded_pair = await uow.pairs.get(pair.id)
        assert loaded_pair == pair
        listed = await uow.rules.list_for_scope(PairScope(pair_id=pair.id))
        assert len(listed) == 1
        assert listed[0].id == rule.id


@pytest.mark.integration
async def test_pair_key_missing_is_data_key_unavailable(
    uow_factory: SqlAlchemyUnitOfWorkFactory,
    engine: AsyncEngine,
) -> None:
    a = _user(82)
    b = _user(83)
    pair = Pair(id=PairId(UUID(int=84)), members=frozenset({a.id, b.id}), created_at=NOW)
    rule = Rule.propose(
        rule_id=RuleId(UUID(int=85)),
        scope=PairScope(pair_id=pair.id),
        category=RuleCategory.TABOO_TOPIC,
        approvers=frozenset({a.id, b.id}),
        author_id=a.id,
        text=RuleText("shared"),
        now=NOW,
    )
    async with uow_factory() as uow:
        await uow.users.add(a)
        await uow.users.add(b)
        await uow.pairs.add(pair)
        await uow.rules.add(rule)
        await uow.commit()
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM pair_keys WHERE pair_id = :id"), {"id": pair.id})
    with pytest.raises(DataKeyUnavailableError):
        async with uow_factory() as uow:
            await uow.rules.get(rule.id)
