"""Tone signal + rule suggestion application tests (0011.1)."""

from __future__ import annotations

from uuid import UUID

import pytest

from svoi_pravila.adapters.system.tone_suggestion_catalog import StaticToneSuggestionCatalog
from svoi_pravila.application.errors import ConflictError, NotFound
from svoi_pravila.application.inline_result_ref import encode_inline_result_ref
from svoi_pravila.application.use_cases.accept_suggestion import (
    AcceptSuggestion,
    AcceptSuggestionCommand,
    AcceptSuggestionOutcome,
)
from svoi_pravila.application.use_cases.create_contact import CreateContact, CreateContactCommand
from svoi_pravila.application.use_cases.dismiss_suggestion import (
    DismissSuggestion,
    DismissSuggestionCommand,
    DismissSuggestionOutcome,
)
from svoi_pravila.application.use_cases.list_suggestions import (
    ListSuggestions,
    ListSuggestionsCommand,
)
from svoi_pravila.application.use_cases.propose_rule import ProposeRule, ProposeRuleCommand
from svoi_pravila.application.use_cases.record_inline_choice import (
    RecordInlineChoice,
    RecordInlineChoiceCommand,
    RecordInlineChoicePorts,
    RecordInlineChoiceResult,
    ToneSignalOutcome,
)
from svoi_pravila.application.use_cases.revoke_all_consents import (
    RevokeAllConsents,
    RevokeAllConsentsCommand,
)
from svoi_pravila.application.use_cases.set_active_contact import (
    SetActiveContact,
    SetActiveContactCommand,
)
from svoi_pravila.domain.contact import Contact
from svoi_pravila.domain.enums import (
    Firmness,
    RelationshipKind,
    RuleCategory,
    RuleStatus,
    SuggestionStatus,
    UsageScenario,
)
from svoi_pravila.domain.ids import ContactId, RuleSuggestionId, TelegramUserId
from svoi_pravila.domain.rule_suggestion import ToneSignal
from svoi_pravila.domain.rules import MAX_OPEN_RULES_PER_SCOPE, ContactScope
from svoi_pravila.domain.text import ContactLabel, RuleText
from svoi_pravila.domain.user import User
from tests.fakes.inline_reuse import make_inline_reuse
from tests.fakes.rate_limit import FakePseudonymizer
from tests.fakes.uow import InMemoryToneSignalRepository
from tests.fakes.usage_sink import RecordingUsageEventSink
from tests.unit.application.conftest import AppWorld


async def _boom_upsert(_self: InMemoryToneSignalRepository, signal: ToneSignal) -> None:
    raise ConflictError()


def _recorder(world: AppWorld, sink: RecordingUsageEventSink | None = None) -> RecordInlineChoice:
    return RecordInlineChoice(
        RecordInlineChoicePorts(
            sink=sink or RecordingUsageEventSink(),
            uow_factory=world.uow_factory,
            catalog=world.catalog,
            tone_catalog=StaticToneSuggestionCatalog(),
            clock=world.clock,
            ids=world.ids,
            pseudonymizer=FakePseudonymizer(),
        )
    )


async def _user_with_active_contact(
    world: AppWorld, telegram_id: int = 501
) -> tuple[User, Contact]:
    user = await world.ensure_granted_user(telegram_id)
    contact = (
        await CreateContact(world.uow_factory, world.catalog, world.ids, world.clock).execute(
            CreateContactCommand(user.id, ContactLabel("Мама"), RelationshipKind.FAMILY)
        )
    ).contact
    user = (
        await SetActiveContact(world.uow_factory, world.catalog).execute(
            SetActiveContactCommand(user.id, contact.id)
        )
    ).user
    return user, contact


async def _choose(
    world: AppWorld, telegram_id: int, firmness: Firmness
) -> RecordInlineChoiceResult:
    return await _recorder(world).execute(
        RecordInlineChoiceCommand(
            TelegramUserId(telegram_id),
            encode_inline_result_ref(UsageScenario.SOFTEN, firmness),
        )
    )


@pytest.mark.unit
async def test_threshold_crossing_creates_exactly_one_suggestion(world: AppWorld) -> None:
    user, contact = await _user_with_active_contact(world, 510)
    for _ in range(4):
        result = await _choose(world, 510, Firmness.GENTLE)
        assert result.suggestion_id is None
        assert result.tone_outcome is ToneSignalOutcome.RECORDED
    result = await _choose(world, 510, Firmness.GENTLE)
    assert result.suggestion_id is not None
    assert result.tone_outcome is ToneSignalOutcome.SUGGESTION_CREATED
    listed = await ListSuggestions(world.uow_factory, world.catalog).execute(
        ListSuggestionsCommand(user.id, contact.id)
    )
    assert len(listed.suggestions) == 1
    assert listed.suggestions[0].firmness is Firmness.GENTLE
    assert listed.suggestions[0].status is SuggestionStatus.PENDING


@pytest.mark.unit
async def test_repeats_do_not_create_another_suggestion(world: AppWorld) -> None:
    await _user_with_active_contact(world, 511)
    for _ in range(5):
        await _choose(world, 511, Firmness.BALANCED)
    second = await _choose(world, 511, Firmness.BALANCED)
    assert second.suggestion_id is None
    assert second.tone_outcome is ToneSignalOutcome.RECORDED
    async with world.uow_factory() as uow:
        user = await uow.users.get_by_telegram_id(TelegramUserId(511))
        assert user is not None
        all_suggestions = await uow.rule_suggestions.list_for_user(user.id)
        assert len(all_suggestions) == 1


@pytest.mark.unit
async def test_pending_tone_does_not_freeze_signal_when_dominant_shifts(world: AppWorld) -> None:
    user, contact = await _user_with_active_contact(world, 522)
    for _ in range(5):
        await _choose(world, 522, Firmness.GENTLE)
    for index in range(10):
        result = await _choose(world, 522, Firmness.FIRM)
        assert result.suggestion_id is None
        assert result.tone_outcome is ToneSignalOutcome.RECORDED
        async with world.uow_factory() as uow:
            signal = await uow.tone_signals.get(user.id, contact.id)
            assert signal is not None
            assert signal.values[-1] is Firmness.FIRM
            assert len(signal.values) == min(5 + index + 1, 10)
    listed = await ListSuggestions(world.uow_factory, world.catalog).execute(
        ListSuggestionsCommand(user.id, contact.id)
    )
    assert len(listed.suggestions) == 1
    assert listed.suggestions[0].firmness is Firmness.GENTLE
    await DismissSuggestion(world.uow_factory, world.catalog, world.clock).execute(
        DismissSuggestionCommand(user.id, listed.suggestions[0].id)
    )
    created = await _choose(world, 522, Firmness.FIRM)
    assert created.tone_outcome is ToneSignalOutcome.SUGGESTION_CREATED
    assert created.suggestion_id is not None
    after = await ListSuggestions(world.uow_factory, world.catalog).execute(
        ListSuggestionsCommand(user.id, contact.id)
    )
    assert len(after.suggestions) == 1
    assert after.suggestions[0].firmness is Firmness.FIRM


@pytest.mark.unit
async def test_dismissed_is_never_re_offered(world: AppWorld) -> None:
    user, contact = await _user_with_active_contact(world, 512)
    for _ in range(5):
        await _choose(world, 512, Firmness.FIRM)
    listed = await ListSuggestions(world.uow_factory, world.catalog).execute(
        ListSuggestionsCommand(user.id, contact.id)
    )
    suggestion = listed.suggestions[0]
    dismissed = await DismissSuggestion(world.uow_factory, world.catalog, world.clock).execute(
        DismissSuggestionCommand(user.id, suggestion.id)
    )
    assert dismissed.outcome is DismissSuggestionOutcome.DISMISSED
    again = await _choose(world, 512, Firmness.FIRM)
    assert again.suggestion_id is None
    listed_after = await ListSuggestions(world.uow_factory, world.catalog).execute(
        ListSuggestionsCommand(user.id, contact.id)
    )
    assert listed_after.suggestions == ()


@pytest.mark.unit
async def test_another_contact_is_independent(world: AppWorld) -> None:
    user, first = await _user_with_active_contact(world, 513)
    second = (
        await CreateContact(world.uow_factory, world.catalog, world.ids, world.clock).execute(
            CreateContactCommand(user.id, ContactLabel("Папа"), RelationshipKind.FAMILY)
        )
    ).contact
    for _ in range(5):
        await _choose(world, 513, Firmness.GENTLE)
    await SetActiveContact(world.uow_factory, world.catalog).execute(
        SetActiveContactCommand(user.id, second.id)
    )
    for _ in range(4):
        result = await _choose(world, 513, Firmness.GENTLE)
        assert result.suggestion_id is None
    result = await _choose(world, 513, Firmness.GENTLE)
    assert result.suggestion_id is not None
    listed_first = await ListSuggestions(world.uow_factory, world.catalog).execute(
        ListSuggestionsCommand(user.id, first.id)
    )
    listed_second = await ListSuggestions(world.uow_factory, world.catalog).execute(
        ListSuggestionsCommand(user.id, second.id)
    )
    assert len(listed_first.suggestions) == 1
    assert len(listed_second.suggestions) == 1


@pytest.mark.unit
async def test_no_active_contact_records_no_signal(world: AppWorld) -> None:
    user = await world.ensure_granted_user(514)
    result = await _choose(world, 514, Firmness.GENTLE)
    assert result.suggestion_id is None
    assert result.tone_outcome is ToneSignalOutcome.SKIPPED_NO_CONTACT
    async with world.uow_factory() as uow:
        signals = await uow.tone_signals.list_for_user(user.id)
        assert signals == []


@pytest.mark.unit
async def test_revoked_records_no_signal(world: AppWorld) -> None:
    user, _contact = await _user_with_active_contact(world, 515)
    await RevokeAllConsents(world.uow_factory, world.clock, make_inline_reuse(world.clock)).execute(
        RevokeAllConsentsCommand(user.telegram_user_id)
    )
    result = await _choose(world, 515, Firmness.GENTLE)
    assert result.suggestion_id is None
    assert result.tone_outcome is ToneSignalOutcome.SKIPPED_NO_ACCESS
    async with world.uow_factory() as uow:
        signals = await uow.tone_signals.list_for_user(user.id)
        assert signals == []


@pytest.mark.unit
async def test_accept_creates_active_rule(world: AppWorld) -> None:
    user, contact = await _user_with_active_contact(world, 516)
    for _ in range(5):
        await _choose(world, 516, Firmness.GENTLE)
    listed = await ListSuggestions(world.uow_factory, world.catalog).execute(
        ListSuggestionsCommand(user.id, contact.id)
    )
    accepted = await AcceptSuggestion(
        world.uow_factory, world.catalog, world.ids, world.clock
    ).execute(AcceptSuggestionCommand(user.id, listed.suggestions[0].id))
    assert accepted.outcome is AcceptSuggestionOutcome.ACCEPTED
    assert accepted.rule is not None
    assert accepted.rule.status is RuleStatus.ACTIVE
    assert accepted.suggestion is not None
    assert accepted.suggestion.status is SuggestionStatus.ACCEPTED


@pytest.mark.unit
async def test_open_rule_limit_keeps_suggestion_pending(world: AppWorld) -> None:
    user, contact = await _user_with_active_contact(world, 517)
    propose = ProposeRule(world.uow_factory, world.catalog, world.ids, world.clock, world.notifier)
    for index in range(MAX_OPEN_RULES_PER_SCOPE):
        await propose.execute(
            ProposeRuleCommand(
                user.id,
                contact.id,
                RuleCategory.OTHER,
                RuleText(f"rule-{index}"),
                shared=False,
            )
        )
    for _ in range(5):
        await _choose(world, 517, Firmness.BALANCED)
    listed = await ListSuggestions(world.uow_factory, world.catalog).execute(
        ListSuggestionsCommand(user.id, contact.id)
    )
    result = await AcceptSuggestion(
        world.uow_factory, world.catalog, world.ids, world.clock
    ).execute(AcceptSuggestionCommand(user.id, listed.suggestions[0].id))
    assert result.outcome is AcceptSuggestionOutcome.OPEN_RULE_LIMIT
    assert result.suggestion is not None
    assert result.suggestion.status is SuggestionStatus.PENDING
    async with world.uow_factory() as uow:
        open_count = await uow.rules.count_open_for_scope(ContactScope(contact_id=contact.id))
        assert open_count == MAX_OPEN_RULES_PER_SCOPE


@pytest.mark.unit
async def test_already_decided_is_typed_outcome(world: AppWorld) -> None:
    user, contact = await _user_with_active_contact(world, 518)
    for _ in range(5):
        await _choose(world, 518, Firmness.GENTLE)
    listed = await ListSuggestions(world.uow_factory, world.catalog).execute(
        ListSuggestionsCommand(user.id, contact.id)
    )
    suggestion_id = listed.suggestions[0].id
    await DismissSuggestion(world.uow_factory, world.catalog, world.clock).execute(
        DismissSuggestionCommand(user.id, suggestion_id)
    )
    again = await DismissSuggestion(world.uow_factory, world.catalog, world.clock).execute(
        DismissSuggestionCommand(user.id, suggestion_id)
    )
    assert again.outcome is DismissSuggestionOutcome.ALREADY_DECIDED
    accept_again = await AcceptSuggestion(
        world.uow_factory, world.catalog, world.ids, world.clock
    ).execute(AcceptSuggestionCommand(user.id, suggestion_id))
    assert accept_again.outcome is AcceptSuggestionOutcome.ALREADY_DECIDED


@pytest.mark.unit
async def test_list_and_decide_unknown_suggestion_not_found(world: AppWorld) -> None:
    user, _contact = await _user_with_active_contact(world, 519)
    stranger = await world.ensure_granted_user(520)
    with pytest.raises(NotFound):
        await ListSuggestions(world.uow_factory, world.catalog).execute(
            ListSuggestionsCommand(stranger.id, _contact.id)
        )
    with pytest.raises(NotFound):
        await AcceptSuggestion(world.uow_factory, world.catalog, world.ids, world.clock).execute(
            AcceptSuggestionCommand(user.id, RuleSuggestionId(UUID(int=999)))
        )
    with pytest.raises(NotFound):
        await DismissSuggestion(world.uow_factory, world.catalog, world.clock).execute(
            DismissSuggestionCommand(user.id, RuleSuggestionId(UUID(int=999)))
        )


@pytest.mark.unit
async def test_tone_signal_conflict_and_missing_contact_outcomes(
    world: AppWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    user, _contact = await _user_with_active_contact(world, 521)
    monkeypatch.setattr(InMemoryToneSignalRepository, "upsert", _boom_upsert)
    result = await _choose(world, 521, Firmness.GENTLE)
    assert result.suggestion_id is None
    assert result.tone_outcome is ToneSignalOutcome.SKIPPED_CONFLICT

    async with world.uow_factory() as uow:
        stored = await uow.users.get(user.id)
        assert stored is not None
        await uow.users.update(stored.set_active_contact(ContactId(UUID(int=404))))
        await uow.commit()
    result = await _choose(world, 521, Firmness.GENTLE)
    assert result.suggestion_id is None
    assert result.tone_outcome is ToneSignalOutcome.SKIPPED_NO_CONTACT
