"""C1: delete account shreds caller data and rehomes partner-authored pair rules."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import MetaData, select, text
from sqlalchemy.ext.asyncio import AsyncEngine

from svoi_pravila.adapters.persistence.uow import SqlAlchemyUnitOfWorkFactory
from svoi_pravila.application.use_cases.delete_my_account import (
    DeleteMyAccount,
    DeleteMyAccountCommand,
    DeleteMyAccountPorts,
)
from svoi_pravila.config import Settings
from svoi_pravila.crypto import HmacPseudonymizer
from svoi_pravila.domain.contact import Contact
from svoi_pravila.domain.enums import (
    RelationshipKind,
    RuleCategory,
    RuleStatus,
    UsageOutcome,
    UsageScenario,
    UsageSurface,
)
from svoi_pravila.domain.ids import (
    ContactId,
    PairId,
    RuleId,
    TelegramUserId,
    UsageEventId,
    UserId,
)
from svoi_pravila.domain.pair import Pair
from svoi_pravila.domain.rules import ContactScope, PairScope, Rule
from svoi_pravila.domain.text import ContactLabel, RuleText
from svoi_pravila.domain.usage import UsageEvent
from svoi_pravila.domain.user import User
from tests.fakes.clock import FakeClock
from tests.fakes.ids import FakeIdGenerator
from tests.fakes.inline_reuse import make_inline_reuse
from tests.fakes.pair_notifier import FakePairNotifier

NOW = datetime(2026, 1, 1, tzinfo=UTC)
LEAVE_AT = NOW + timedelta(days=5)


def _user(n: int) -> User:
    return User(
        id=UserId(UUID(int=n)),
        telegram_user_id=TelegramUserId(2000 + n),
        created_at=NOW,
        age_confirmed_at=NOW,
        active_contact_id=None,
    )


async def _scan_has_uuid(engine: AsyncEngine, marker: UUID) -> bool:
    metadata = MetaData()
    async with engine.connect() as conn:
        await conn.run_sync(metadata.reflect)
        needle = str(marker)
        for table in metadata.tables.values():
            rows = (await conn.execute(select(*table.columns))).all()
            for row in rows:
                for value in row:
                    if value == marker:
                        return True
                    if isinstance(value, (bytes, memoryview)):
                        if needle.encode("utf-8") in bytes(value):
                            return True
                    elif needle in str(value):
                        return True
    return False


def _usage(event_id: int, pseudonym: str) -> UsageEvent:
    return UsageEvent(
        id=UsageEventId(UUID(int=event_id)),
        occurred_at=NOW,
        user_pseudonym=pseudonym,
        scenario=UsageScenario.DECODE,
        surface=UsageSurface.DM,
        outcome=UsageOutcome.OK,
        unavailable_kind=None,
        safety="ok",
        model="m",
        prompt_version="p",
        latency_ms=1,
        ttfc_ms=None,
        attempts=1,
        input_tokens=0,
        output_tokens=0,
        billable_tokens=0,
    )


async def _seed_delete_fixture(
    uow_factory: SqlAlchemyUnitOfWorkFactory,
    *,
    a_pseudo: str,
    b_pseudo: str,
) -> tuple[User, User, Contact, Contact, Pair]:
    alice = _user(1)
    bob = _user(2)
    alice_contact = Contact(
        id=ContactId(UUID(int=10)),
        owner_id=alice.id,
        label=ContactLabel("Bob-label"),
        relationship=RelationshipKind.PARTNER,
        pair_id=None,
        created_at=NOW,
    )
    bob_contact = Contact(
        id=ContactId(UUID(int=11)),
        owner_id=bob.id,
        label=ContactLabel("Alice-label"),
        relationship=RelationshipKind.PARTNER,
        pair_id=None,
        created_at=NOW,
    )
    pair = Pair(
        id=PairId(UUID(int=20)),
        members=frozenset({alice.id, bob.id}),
        created_at=NOW,
    )
    alice_private = Rule.propose(
        rule_id=RuleId(UUID(int=30)),
        scope=ContactScope(contact_id=alice_contact.id),
        category=RuleCategory.OTHER,
        approvers=frozenset({alice.id}),
        author_id=alice.id,
        text=RuleText("alice private rule"),
        now=NOW,
    )
    bob_private = Rule.propose(
        rule_id=RuleId(UUID(int=31)),
        scope=ContactScope(contact_id=bob_contact.id),
        category=RuleCategory.OTHER,
        approvers=frozenset({bob.id}),
        author_id=bob.id,
        text=RuleText("bob private rule"),
        now=NOW,
    )
    alice_shared = Rule.propose(
        rule_id=RuleId(UUID(int=32)),
        scope=PairScope(pair_id=pair.id),
        category=RuleCategory.OTHER,
        approvers=frozenset({alice.id, bob.id}),
        author_id=alice.id,
        text=RuleText("alice shared rule"),
        now=NOW,
    ).approve(bob.id, NOW)
    bob_shared = Rule.propose(
        rule_id=RuleId(UUID(int=33)),
        scope=PairScope(pair_id=pair.id),
        category=RuleCategory.OTHER,
        approvers=frozenset({alice.id, bob.id}),
        author_id=bob.id,
        text=RuleText("bob shared rule"),
        now=NOW,
    ).approve(alice.id, NOW)
    bob_pending = Rule.propose(
        rule_id=RuleId(UUID(int=34)),
        scope=PairScope(pair_id=pair.id),
        category=RuleCategory.OTHER,
        approvers=frozenset({alice.id, bob.id}),
        author_id=bob.id,
        text=RuleText("bob pending pair rule"),
        now=NOW,
    )
    async with uow_factory() as uow:
        await uow.users.add(alice)
        await uow.users.add(bob)
        await uow.contacts.add(alice_contact)
        await uow.contacts.add(bob_contact)
        await uow.pairs.add(pair)
        await uow.contacts.update(alice_contact.link_pair(pair.id))
        await uow.contacts.update(bob_contact.link_pair(pair.id))
        await uow.rules.add(alice_private)
        await uow.rules.add(bob_private)
        await uow.rules.add(alice_shared)
        await uow.rules.add(bob_shared)
        await uow.rules.add(bob_pending)
        await uow.usage_events.add(_usage(40, a_pseudo))
        await uow.usage_events.add(_usage(41, b_pseudo))
        await uow.commit()
    return alice, bob, alice_contact, bob_contact, pair


async def _insert_daily_aggregate(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO analytics_daily ("
                "day, active_users, appeals, new_users, generations, "
                "generation_errors, computed_at"
                ") VALUES ("
                "DATE '2026-01-01', 1, 1, 1, 1, 0, :now"
                ")"
            ),
            {"now": NOW},
        )


@pytest.mark.integration
async def test_delete_account_shreds_caller_and_rehomes_partner_rules(
    settings: Settings,
    engine: AsyncEngine,
    uow_factory_postgres: SqlAlchemyUnitOfWorkFactory,
) -> None:
    pepper = HmacPseudonymizer(settings.pseudonym_pepper_bytes())
    alice_preview = _user(1)
    bob_preview = _user(2)
    a_pseudo = pepper.pseudonymize("analytics", str(alice_preview.telegram_user_id.value))
    b_pseudo = pepper.pseudonymize("analytics", str(bob_preview.telegram_user_id.value))
    alice, bob, alice_contact, bob_contact, pair = await _seed_delete_fixture(
        uow_factory_postgres, a_pseudo=a_pseudo, b_pseudo=b_pseudo
    )
    await _insert_daily_aggregate(engine)
    clock = FakeClock(LEAVE_AT)
    result = await DeleteMyAccount(
        DeleteMyAccountPorts(
            uow_factory_postgres,
            FakeIdGenerator(),
            pepper,
            clock,
            make_inline_reuse(clock),
            FakePairNotifier(),
        )
    ).execute(DeleteMyAccountCommand(alice.telegram_user_id))
    assert result.found is True
    assert await _scan_has_uuid(engine, alice.id) is False
    async with engine.connect() as conn:
        alice_keys = (
            await conn.execute(
                text("SELECT count(*) FROM user_keys WHERE user_id = :id"), {"id": alice.id}
            )
        ).scalar_one()
        pair_keys = (
            await conn.execute(
                text("SELECT count(*) FROM pair_keys WHERE pair_id = :id"), {"id": pair.id}
            )
        ).scalar_one()
        pair_rows = (
            await conn.execute(text("SELECT count(*) FROM pairs WHERE id = :id"), {"id": pair.id})
        ).scalar_one()
        a_usage = (
            await conn.execute(
                text("SELECT count(*) FROM usage_events WHERE user_pseudonym = :p"),
                {"p": a_pseudo},
            )
        ).scalar_one()
        b_usage = (
            await conn.execute(
                text("SELECT count(*) FROM usage_events WHERE user_pseudonym = :p"),
                {"p": b_pseudo},
            )
        ).scalar_one()
    assert alice_keys == 0
    assert pair_keys == 0
    assert pair_rows == 0
    assert a_usage == 0
    assert b_usage == 1
    async with engine.connect() as conn:
        daily = (
            await conn.execute(
                text("SELECT active_users FROM analytics_daily WHERE day = DATE '2026-01-01'")
            )
        ).scalar_one()
    assert daily == 1

    async with uow_factory_postgres() as uow:
        assert await uow.contacts.get(alice_contact.id) is None
        remaining = await uow.contacts.get(bob_contact.id)
        assert remaining is not None
        assert remaining.pair_id is None
        rules = await uow.rules.list_for_scope(ContactScope(contact_id=remaining.id))
        texts = {rule.revisions[-1].text.value for rule in rules}
        assert texts == {"bob private rule", "bob shared rule", "bob pending pair rule"}
        by_text = {rule.revisions[-1].text.value: rule for rule in rules}
        pending = by_text["bob pending pair rule"]
        assert pending.revisions[0].effective_since == clock.now()
        assert pending.revisions[0].effective_since == LEAVE_AT
        assert pending.revisions[0].effective_since >= LEAVE_AT
        assert by_text["bob shared rule"].revisions[0].effective_since == NOW
        assert all(rule.status is RuleStatus.ACTIVE for rule in rules)
        assert await uow.users.get(bob.id) is not None
        assert await uow.users.get(alice.id) is None
        assert await uow.rules.list_for_scope(PairScope(pair_id=pair.id)) == []
