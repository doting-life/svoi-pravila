"""Postgres integration: tone signals, suggestions, freeze fix, concurrency."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from svoi_pravila.adapters.persistence.uow import SqlAlchemyUnitOfWorkFactory
from svoi_pravila.adapters.system.tone_suggestion_catalog import StaticToneSuggestionCatalog
from svoi_pravila.application.errors import ConflictError
from svoi_pravila.application.inline_result_ref import encode_inline_result_ref
from svoi_pravila.application.use_cases.accept_age_confirmation import (
    AcceptAgeConfirmation,
    AcceptAgeConfirmationCommand,
)
from svoi_pravila.application.use_cases.create_contact import CreateContact, CreateContactCommand
from svoi_pravila.application.use_cases.delete_my_account import (
    DeleteMyAccount,
    DeleteMyAccountCommand,
)
from svoi_pravila.application.use_cases.dismiss_suggestion import (
    DismissSuggestion,
    DismissSuggestionCommand,
)
from svoi_pravila.application.use_cases.export_my_data import ExportMyData, ExportMyDataCommand
from svoi_pravila.application.use_cases.grant_consent import GrantConsent, GrantConsentCommand
from svoi_pravila.application.use_cases.list_suggestions import (
    ListSuggestions,
    ListSuggestionsCommand,
)
from svoi_pravila.application.use_cases.record_inline_choice import (
    RecordInlineChoice,
    RecordInlineChoiceCommand,
    RecordInlineChoicePorts,
    ToneSignalOutcome,
)
from svoi_pravila.domain.contact import Contact
from svoi_pravila.domain.enums import (
    ConsentKind,
    Firmness,
    RelationshipKind,
    RuleCategory,
    SuggestionSource,
    SuggestionStatus,
    UsageScenario,
)
from svoi_pravila.domain.ids import ContactId, RuleSuggestionId, TelegramUserId, UserId
from svoi_pravila.domain.rule_suggestion import RuleSuggestion, ToneSignal
from svoi_pravila.domain.text import ContactLabel, RuleText
from svoi_pravila.domain.user import User
from tests.fakes.clock import FakeClock
from tests.fakes.consent_catalog import FakeConsentCatalog
from tests.fakes.ids import FakeIdGenerator
from tests.fakes.inline_reuse import make_inline_reuse
from tests.fakes.rate_limit import FakePseudonymizer
from tests.fakes.usage_sink import RecordingUsageEventSink

NOW = datetime(2026, 10, 5, 12, tzinfo=UTC)


@dataclass(slots=True)
class _ToneWorld:
    uow_factory: SqlAlchemyUnitOfWorkFactory
    catalog: FakeConsentCatalog
    clock: FakeClock
    ids: FakeIdGenerator

    def choose(self) -> RecordInlineChoice:
        return RecordInlineChoice(
            RecordInlineChoicePorts(
                sink=RecordingUsageEventSink(),
                uow_factory=self.uow_factory,
                catalog=self.catalog,
                tone_catalog=StaticToneSuggestionCatalog(),
                clock=self.clock,
                ids=self.ids,
                pseudonymizer=FakePseudonymizer(),
            )
        )

    async def granted_with_contact(
        self, telegram_id: int, label: str = "Мама"
    ) -> tuple[User, ContactId]:
        accepted = await AcceptAgeConfirmation(self.uow_factory, self.ids, self.clock).execute(
            AcceptAgeConfirmationCommand(TelegramUserId(telegram_id))
        )
        for kind in ConsentKind:
            version = self.catalog.current_requirement().for_kind(kind).version
            await GrantConsent(self.uow_factory, self.catalog, self.ids, self.clock).execute(
                GrantConsentCommand(accepted.user.id, kind, version)
            )
        created = await CreateContact(self.uow_factory, self.catalog, self.ids, self.clock).execute(
            CreateContactCommand(accepted.user.id, ContactLabel(label), RelationshipKind.FAMILY)
        )
        async with self.uow_factory() as uow:
            user = await uow.users.get(accepted.user.id)
            assert user is not None
            assert user.active_contact_id == created.contact.id
            return user, created.contact.id


def _world(uow_factory: SqlAlchemyUnitOfWorkFactory) -> _ToneWorld:
    return _ToneWorld(
        uow_factory=uow_factory,
        catalog=FakeConsentCatalog(),
        clock=FakeClock(start=NOW),
        ids=FakeIdGenerator(),
    )


@pytest.mark.integration
async def test_tone_signal_does_not_freeze_while_pending(
    uow_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    world = _world(uow_factory)
    user, contact_id = await world.granted_with_contact(9011)
    choose = world.choose()
    ref_gentle = encode_inline_result_ref(UsageScenario.SOFTEN, Firmness.GENTLE)
    ref_firm = encode_inline_result_ref(UsageScenario.SOFTEN, Firmness.FIRM)

    for _ in range(5):
        result = await choose.execute(RecordInlineChoiceCommand(TelegramUserId(9011), ref_gentle))
    assert result.tone_outcome is ToneSignalOutcome.SUGGESTION_CREATED
    pending = await ListSuggestions(uow_factory, world.catalog).execute(
        ListSuggestionsCommand(user.id, contact_id)
    )
    assert len(pending.suggestions) == 1
    assert pending.suggestions[0].firmness is Firmness.GENTLE

    for index in range(10):
        result = await choose.execute(RecordInlineChoiceCommand(TelegramUserId(9011), ref_firm))
        assert result.suggestion_id is None
        assert result.tone_outcome is ToneSignalOutcome.RECORDED
        async with uow_factory() as uow:
            signal = await uow.tone_signals.get(user.id, contact_id)
            assert signal is not None
            assert signal.values[-1] is Firmness.FIRM
            assert len(signal.values) == min(5 + index + 1, 10)

    await DismissSuggestion(uow_factory, world.catalog, world.clock).execute(
        DismissSuggestionCommand(user.id, pending.suggestions[0].id)
    )
    created = await choose.execute(RecordInlineChoiceCommand(TelegramUserId(9011), ref_firm))
    assert created.tone_outcome is ToneSignalOutcome.SUGGESTION_CREATED
    assert created.suggestion_id is not None
    after = await ListSuggestions(uow_factory, world.catalog).execute(
        ListSuggestionsCommand(user.id, contact_id)
    )
    assert len(after.suggestions) == 1
    assert after.suggestions[0].firmness is Firmness.FIRM


@pytest.mark.integration
async def test_concurrent_tone_signal_appends_keep_both_values(
    uow_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    world = _world(uow_factory)
    user, contact_id = await world.granted_with_contact(9012, label="Папа")
    choose_a = world.choose()
    other_ids = FakeIdGenerator()
    for _ in range(50):
        other_ids.new_id()
    choose_b = RecordInlineChoice(
        RecordInlineChoicePorts(
            sink=RecordingUsageEventSink(),
            uow_factory=uow_factory,
            catalog=world.catalog,
            tone_catalog=StaticToneSuggestionCatalog(),
            clock=world.clock,
            ids=other_ids,
            pseudonymizer=FakePseudonymizer(),
        )
    )
    ref_gentle = encode_inline_result_ref(UsageScenario.SOFTEN, Firmness.GENTLE)
    ref_firm = encode_inline_result_ref(UsageScenario.SOFTEN, Firmness.FIRM)
    await asyncio.gather(
        choose_a.execute(RecordInlineChoiceCommand(TelegramUserId(9012), ref_gentle)),
        choose_b.execute(RecordInlineChoiceCommand(TelegramUserId(9012), ref_firm)),
    )
    async with uow_factory() as uow:
        signal = await uow.tone_signals.get(user.id, contact_id)
        assert signal is not None
        assert set(signal.values) == {Firmness.GENTLE, Firmness.FIRM}
        assert len(signal.values) == 2


@pytest.mark.integration
async def test_suggestion_and_tone_repository_crud(
    uow_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    owner = User(
        id=UserId(UUID(int=9100)),
        telegram_user_id=TelegramUserId(9100),
        created_at=NOW,
        age_confirmed_at=NOW,
        active_contact_id=None,
    )
    contact_id = ContactId(UUID(int=9101))
    contact = Contact(
        id=contact_id,
        owner_id=owner.id,
        label=ContactLabel("Repo"),
        relationship=RelationshipKind.FRIEND,
        pair_id=None,
        created_at=NOW,
    )
    suggestion = RuleSuggestion.create_tone(
        suggestion_id=RuleSuggestionId(UUID(int=9102)),
        user_id=owner.id,
        contact_id=contact_id,
        category=RuleCategory.HOW_TO_ASK,
        text=RuleText("Говорить мягко, без резких формулировок"),
        firmness=Firmness.GENTLE,
        now=NOW,
    )
    async with uow_factory() as uow:
        await uow.users.add(owner)
        await uow.contacts.add(contact)
        assert await uow.tone_signals.get(owner.id, contact_id) is None
        locked = await uow.tone_signals.lock_for_append(owner.id, contact_id)
        assert locked.values == ()
        await uow.tone_signals.upsert(
            ToneSignal(user_id=owner.id, contact_id=contact_id, values=(Firmness.GENTLE,))
        )
        await uow.rule_suggestions.add(suggestion)
        assert await uow.rule_suggestions.has_pending_for_source(
            owner.id, contact_id, SuggestionSource.TONE
        )
        assert await uow.rule_suggestions.get(suggestion.id) is not None
        assert (
            await uow.rule_suggestions.get_tone(owner.id, contact_id, Firmness.GENTLE)
        ) is not None
        assert len(await uow.rule_suggestions.list_pending_for_contact(owner.id, contact_id)) == 1
        assert len(await uow.rule_suggestions.list_for_user(owner.id)) == 1
        assert len(await uow.tone_signals.list_for_user(owner.id)) == 1
        await uow.commit()

    async with uow_factory() as uow1:
        loaded1 = await uow1.rule_suggestions.get(suggestion.id)
        assert loaded1 is not None
        async with uow_factory() as uow2:
            loaded2 = await uow2.rule_suggestions.get(suggestion.id)
            assert loaded2 is not None
            await uow2.rule_suggestions.update(loaded2.dismiss(NOW))
            await uow2.commit()
        with pytest.raises(ConflictError):
            await uow1.rule_suggestions.update(loaded1.accept(NOW))
            await uow1.commit()

    async with uow_factory() as uow:
        assert not await uow.rule_suggestions.has_pending_for_source(
            owner.id, contact_id, SuggestionSource.TONE
        )
        decided = await uow.rule_suggestions.get(suggestion.id)
        assert decided is not None
        assert decided.status is SuggestionStatus.DISMISSED


@pytest.mark.integration
async def test_suggestion_export_and_delete_clear_rows(
    uow_factory: SqlAlchemyUnitOfWorkFactory,
    engine: AsyncEngine,
) -> None:
    clock = FakeClock(start=NOW)
    ids = FakeIdGenerator()
    owner = User(
        id=UserId(UUID(int=9200)),
        telegram_user_id=TelegramUserId(9200),
        created_at=NOW,
        age_confirmed_at=NOW,
        active_contact_id=None,
    )
    contact_id = ContactId(UUID(int=9201))
    contact = Contact(
        id=contact_id,
        owner_id=owner.id,
        label=ContactLabel("Export"),
        relationship=RelationshipKind.FRIEND,
        pair_id=None,
        created_at=NOW,
    )
    suggestion = RuleSuggestion.create_tone(
        suggestion_id=RuleSuggestionId(UUID(int=9202)),
        user_id=owner.id,
        contact_id=contact_id,
        category=RuleCategory.HOW_TO_ASK,
        text=RuleText("Говорить спокойно и по делу"),
        firmness=Firmness.BALANCED,
        now=NOW,
    )
    async with uow_factory() as uow:
        await uow.users.add(owner)
        await uow.contacts.add(contact)
        await uow.tone_signals.upsert(
            ToneSignal(user_id=owner.id, contact_id=contact_id, values=(Firmness.BALANCED,) * 5)
        )
        await uow.rule_suggestions.add(suggestion)
        await uow.commit()

    export = await ExportMyData(uow_factory, clock).execute(
        ExportMyDataCommand(TelegramUserId(9200))
    )
    assert export.found
    assert export.payload is not None
    contacts = export.payload["контакты"]
    assert isinstance(contacts, list)
    assert contacts[0]["предложения"][0]["статус"] == SuggestionStatus.PENDING.value
    assert contacts[0]["сигналы_тона"][0]["значения"] == [Firmness.BALANCED.value] * 5

    await DeleteMyAccount(
        uow_factory, ids, FakePseudonymizer(), clock, make_inline_reuse(clock)
    ).execute(DeleteMyAccountCommand(TelegramUserId(9200)))
    async with engine.connect() as conn:
        sug_count = (await conn.execute(text("SELECT count(*) FROM rule_suggestions"))).scalar_one()
        tone_count = (await conn.execute(text("SELECT count(*) FROM tone_signals"))).scalar_one()
        assert int(sug_count) == 0
        assert int(tone_count) == 0
    async with uow_factory() as uow:
        assert await uow.rule_suggestions.list_for_user(owner.id) == []
        assert await uow.tone_signals.list_for_user(owner.id) == []
        assert await uow.rule_suggestions.get(suggestion.id) is None
        assert await uow.rule_suggestions.get_tone(owner.id, contact_id, Firmness.BALANCED) is None
        assert await uow.tone_signals.get(owner.id, contact_id) is None
        await uow.rule_suggestions.delete_for_user(owner.id)
        await uow.tone_signals.delete_for_user(owner.id)


@pytest.mark.integration
async def test_concurrent_pending_decode_suggestions_conflict(
    uow_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    owner = User(
        id=UserId(UUID(int=9300)),
        telegram_user_id=TelegramUserId(9300),
        created_at=NOW,
        age_confirmed_at=NOW,
        active_contact_id=None,
    )
    contact_id = ContactId(UUID(int=9301))
    contact = Contact(
        id=contact_id,
        owner_id=owner.id,
        label=ContactLabel("DecodeRace"),
        relationship=RelationshipKind.FRIEND,
        pair_id=None,
        created_at=NOW,
    )
    first = RuleSuggestion.create_decode(
        suggestion_id=RuleSuggestionId(UUID(int=9302)),
        user_id=owner.id,
        contact_id=contact_id,
        category=RuleCategory.APOLOGY,
        text=RuleText("Мы извиняемся без оговорок"),
        now=NOW,
    )
    second = RuleSuggestion.create_decode(
        suggestion_id=RuleSuggestionId(UUID(int=9303)),
        user_id=owner.id,
        contact_id=contact_id,
        category=RuleCategory.HOW_TO_ASK,
        text=RuleText("Мы говорим спокойно и по делу"),
        now=NOW,
    )
    async with uow_factory() as uow:
        await uow.users.add(owner)
        await uow.contacts.add(contact)
        await uow.commit()

    async def _add(suggestion: RuleSuggestion) -> str:
        try:
            async with uow_factory() as uow:
                await uow.rule_suggestions.add(suggestion)
                await uow.commit()
        except ConflictError:
            return "conflict"
        else:
            return "ok"

    outcomes = await asyncio.gather(_add(first), _add(second))
    assert outcomes.count("ok") == 1
    assert outcomes.count("conflict") == 1

    async with uow_factory() as uow:
        pending = await uow.rule_suggestions.list_pending_for_contact(owner.id, contact_id)
        decode_pending = [s for s in pending if s.source is SuggestionSource.DECODE]
        assert len(decode_pending) == 1
