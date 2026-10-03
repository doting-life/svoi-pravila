"""Persistence encryption, AAD binding, and concurrency integration tests."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from svoi_pravila.adapters.persistence.errors import DataKeyUnavailableError
from svoi_pravila.adapters.persistence.uow import SqlAlchemyUnitOfWorkFactory
from svoi_pravila.application.errors import ConflictError
from svoi_pravila.config import Settings
from svoi_pravila.crypto import DecryptionError
from svoi_pravila.domain.contact import Contact
from svoi_pravila.domain.enums import RelationshipKind, RuleCategory
from svoi_pravila.domain.ids import ContactId, InviteId, PairId, RuleId, TelegramUserId, UserId
from svoi_pravila.domain.invite import Invite, InviteTokenHash
from svoi_pravila.domain.pair import Pair
from svoi_pravila.domain.rules import ContactScope, PairScope, Rule
from svoi_pravila.domain.text import ContactLabel, RuleText
from svoi_pravila.domain.user import User
from tests.support.postgres import (
    create_temporary_database,
    downgrade_base,
    drop_temporary_database,
    upgrade_head,
)

NOW = datetime(2026, 1, 1, tzinfo=UTC)
MARKER_LABEL = "PLAINTEXT-LABEL-MARKER-ZZZ"
MARKER_RULE = "PLAINTEXT-RULE-MARKER-ZZZ"


def _user(n: int) -> User:
    return User(
        id=UserId(UUID(int=n)),
        telegram_user_id=TelegramUserId(1000 + n),
        created_at=NOW,
        age_confirmed_at=NOW,
        active_contact_id=None,
    )


@pytest.mark.integration
async def test_database_has_no_plaintext_c2_markers(
    uow_factory: SqlAlchemyUnitOfWorkFactory,
    engine: AsyncEngine,
) -> None:
    owner = _user(1)
    contact = Contact(
        id=ContactId(UUID(int=10)),
        owner_id=owner.id,
        label=ContactLabel(MARKER_LABEL),
        relationship=RelationshipKind.FRIEND,
        pair_id=None,
        created_at=NOW,
    )
    rule = Rule.propose(
        rule_id=RuleId(UUID(int=20)),
        scope=ContactScope(contact_id=contact.id),
        category=RuleCategory.OTHER,
        approvers=frozenset({owner.id}),
        author_id=owner.id,
        text=RuleText(MARKER_RULE),
        now=NOW,
    )
    async with uow_factory() as uow:
        await uow.users.add(owner)
        await uow.contacts.add(contact)
        await uow.rules.add(rule)
        await uow.commit()

    async with engine.connect() as conn:
        labels = (
            (await conn.execute(text("SELECT encode(label_ciphertext, 'escape') FROM contacts")))
            .scalars()
            .all()
        )
        texts = (
            (
                await conn.execute(
                    text("SELECT encode(text_ciphertext, 'escape') FROM rule_revisions")
                )
            )
            .scalars()
            .all()
        )
    joined = " ".join([*labels, *texts])
    assert MARKER_LABEL not in joined
    assert MARKER_RULE not in joined


@pytest.mark.integration
async def test_swapped_ciphertext_fails_aad_binding(
    uow_factory: SqlAlchemyUnitOfWorkFactory,
    engine: AsyncEngine,
) -> None:
    a = _user(2)
    b = _user(3)
    ca = Contact(
        id=ContactId(UUID(int=11)),
        owner_id=a.id,
        label=ContactLabel("Alice"),
        relationship=RelationshipKind.FRIEND,
        pair_id=None,
        created_at=NOW,
    )
    cb = Contact(
        id=ContactId(UUID(int=12)),
        owner_id=b.id,
        label=ContactLabel("Bob"),
        relationship=RelationshipKind.FRIEND,
        pair_id=None,
        created_at=NOW,
    )
    async with uow_factory() as uow:
        await uow.users.add(a)
        await uow.users.add(b)
        await uow.contacts.add(ca)
        await uow.contacts.add(cb)
        await uow.commit()

    async with engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE contacts AS x SET label_ciphertext = y.label_ciphertext "
                "FROM contacts AS y WHERE x.id = :a AND y.id = :b"
            ),
            {"a": ca.id, "b": cb.id},
        )

    with pytest.raises(DecryptionError):
        async with uow_factory() as uow:
            await uow.contacts.get(ca.id)


@pytest.mark.integration
async def test_wrong_kek_cannot_read_existing_data(
    uow_factory: SqlAlchemyUnitOfWorkFactory,
    engine: AsyncEngine,
) -> None:
    owner = _user(4)
    contact = Contact(
        id=ContactId(UUID(int=13)),
        owner_id=owner.id,
        label=ContactLabel("Secret"),
        relationship=RelationshipKind.OTHER,
        pair_id=None,
        created_at=NOW,
    )
    async with uow_factory() as uow:
        await uow.users.add(owner)
        await uow.contacts.add(contact)
        await uow.commit()

    wrong = SqlAlchemyUnitOfWorkFactory(
        engine,
        kek=b"\x99" * 32,
        kek_id="wrong-1",
    )
    with pytest.raises(DecryptionError):
        async with wrong() as uow:
            await uow.contacts.get(contact.id)


@pytest.mark.integration
async def test_deleting_user_keys_makes_labels_unreadable(
    uow_factory: SqlAlchemyUnitOfWorkFactory,
    engine: AsyncEngine,
) -> None:
    owner = _user(5)
    contact = Contact(
        id=ContactId(UUID(int=14)),
        owner_id=owner.id,
        label=ContactLabel("Shredme"),
        relationship=RelationshipKind.OTHER,
        pair_id=None,
        created_at=NOW,
    )
    async with uow_factory() as uow:
        await uow.users.add(owner)
        await uow.contacts.add(contact)
        await uow.commit()

    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM user_keys WHERE user_id = :id"), {"id": owner.id})

    with pytest.raises(DataKeyUnavailableError):
        async with uow_factory() as uow:
            await uow.contacts.get(contact.id)


@pytest.mark.integration
async def test_contact_label_ciphertext_stable_across_unrelated_update(
    uow_factory: SqlAlchemyUnitOfWorkFactory,
    engine: AsyncEngine,
) -> None:
    owner = _user(50)
    contact = Contact(
        id=ContactId(UUID(int=51)),
        owner_id=owner.id,
        label=ContactLabel("Stable"),
        relationship=RelationshipKind.FRIEND,
        pair_id=None,
        created_at=NOW,
    )
    async with uow_factory() as uow:
        await uow.users.add(owner)
        await uow.contacts.add(contact)
        await uow.commit()

    async with engine.connect() as conn:
        before = (
            await conn.execute(
                text("SELECT label_ciphertext FROM contacts WHERE id = :id"),
                {"id": contact.id},
            )
        ).scalar_one()

    async with uow_factory() as uow:
        loaded = await uow.contacts.get(contact.id)
        assert loaded is not None
        await uow.contacts.update(replace(loaded, relationship=RelationshipKind.WORK))
        await uow.commit()

    async with engine.connect() as conn:
        after = (
            await conn.execute(
                text("SELECT label_ciphertext FROM contacts WHERE id = :id"),
                {"id": contact.id},
            )
        ).scalar_one()
    assert after == before


@pytest.mark.integration
async def test_rule_revision_ciphertext_stable_across_approve(
    uow_factory: SqlAlchemyUnitOfWorkFactory,
    engine: AsyncEngine,
) -> None:
    a = _user(52)
    b = _user(53)
    pair = Pair(
        id=PairId(UUID(int=54)),
        members=frozenset({a.id, b.id}),
        created_at=NOW,
    )
    rule = Rule.propose(
        rule_id=RuleId(UUID(int=55)),
        scope=PairScope(pair_id=pair.id),
        category=RuleCategory.TABOO_TOPIC,
        approvers=frozenset({a.id, b.id}),
        author_id=a.id,
        text=RuleText("immutable text"),
        now=NOW,
    )
    async with uow_factory() as uow:
        await uow.users.add(a)
        await uow.users.add(b)
        await uow.pairs.add(pair)
        await uow.rules.add(rule)
        await uow.commit()

    async with engine.connect() as conn:
        before = (
            await conn.execute(
                text(
                    "SELECT text_ciphertext FROM rule_revisions WHERE rule_id = :id AND number = 1"
                ),
                {"id": rule.id},
            )
        ).scalar_one()

    async with uow_factory() as uow:
        loaded = await uow.rules.get(rule.id)
        assert loaded is not None
        await uow.rules.update(loaded.approve(b.id, NOW + timedelta(seconds=1)))
        await uow.commit()

    async with engine.connect() as conn:
        after = (
            await conn.execute(
                text(
                    "SELECT text_ciphertext FROM rule_revisions WHERE rule_id = :id AND number = 1"
                ),
                {"id": rule.id},
            )
        ).scalar_one()
    assert after == before


@pytest.mark.integration
async def test_concurrent_rule_approve_one_conflict(
    uow_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    a = _user(6)
    b = _user(7)
    pair = Pair(
        id=PairId(UUID(int=30)),
        members=frozenset({a.id, b.id}),
        created_at=NOW,
    )
    contact = Contact(
        id=ContactId(UUID(int=15)),
        owner_id=a.id,
        label=ContactLabel("Partner"),
        relationship=RelationshipKind.PARTNER,
        pair_id=pair.id,
        created_at=NOW,
    )
    rule = Rule.propose(
        rule_id=RuleId(UUID(int=21)),
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
        await uow.contacts.add(contact)
        await uow.rules.add(rule)
        await uow.commit()

    async with uow_factory() as uow1:
        loaded1 = await uow1.rules.get(rule.id)
        assert loaded1 is not None
        async with uow_factory() as uow2:
            loaded2 = await uow2.rules.get(rule.id)
            assert loaded2 is not None
            updated2 = loaded2.approve(b.id, NOW)
            await uow2.rules.update(updated2)
            await uow2.commit()
        updated1 = loaded1.approve(b.id, NOW)
        with pytest.raises(ConflictError):
            await uow1.rules.update(updated1)
            await uow1.commit()


@pytest.mark.integration
async def test_concurrent_pending_edit_approve_vs_reject(
    uow_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    a = _user(60)
    b = _user(61)
    pair = Pair(
        id=PairId(UUID(int=62)),
        members=frozenset({a.id, b.id}),
        created_at=NOW,
    )
    rule = (
        Rule.propose(
            rule_id=RuleId(UUID(int=63)),
            scope=PairScope(pair_id=pair.id),
            category=RuleCategory.TABOO_TOPIC,
            approvers=frozenset({a.id, b.id}),
            author_id=a.id,
            text=RuleText("v1"),
            now=NOW,
        )
        .approve(b.id, NOW + timedelta(seconds=1))
        .propose_edit(a.id, RuleText("v2"), NOW + timedelta(seconds=2))
    )
    async with uow_factory() as uow:
        await uow.users.add(a)
        await uow.users.add(b)
        await uow.pairs.add(pair)
        await uow.rules.add(rule)
        await uow.commit()

    async with uow_factory() as uow1:
        loaded1 = await uow1.rules.get(rule.id)
        assert loaded1 is not None
        async with uow_factory() as uow2:
            loaded2 = await uow2.rules.get(rule.id)
            assert loaded2 is not None
            await uow2.rules.update(loaded2.approve(b.id, NOW + timedelta(seconds=3)))
            await uow2.commit()
        with pytest.raises(ConflictError):
            await uow1.rules.update(loaded1.reject_pending(b.id, NOW + timedelta(seconds=3)))
            await uow1.commit()


@pytest.mark.integration
async def test_concurrent_invite_accept_one_succeeds(
    uow_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    inviter = _user(8)
    invitee_a = _user(9)
    invitee_b = _user(10)
    contact = Contact(
        id=ContactId(UUID(int=16)),
        owner_id=inviter.id,
        label=ContactLabel("Invite"),
        relationship=RelationshipKind.FRIEND,
        pair_id=None,
        created_at=NOW,
    )
    invite = Invite.create(
        invite_id=InviteId(UUID(int=40)),
        inviter_id=inviter.id,
        contact_id=contact.id,
        token_hash=InviteTokenHash.from_raw_token("race-token"),
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
            accepted2 = i2.accept(invitee_b.id, NOW)
            await uow2.invites.update(accepted2)
            await uow2.commit()
        accepted1 = i1.accept(invitee_a.id, NOW)
        with pytest.raises(ConflictError):
            await uow1.invites.update(accepted1)
            await uow1.commit()


@pytest.mark.integration
def test_migration_upgrade_downgrade_upgrade_on_temp_database(
    settings: Settings,
) -> None:
    # Alembic uses asyncio.run; keep this test sync and isolate Alembic in threads.
    temp_url, temp_name = asyncio.run(create_temporary_database(settings))
    try:
        upgrade_head(sqlalchemy_url=temp_url)
        downgrade_base(sqlalchemy_url=temp_url)
        upgrade_head(sqlalchemy_url=temp_url)
    finally:
        asyncio.run(drop_temporary_database(settings, temp_name))
