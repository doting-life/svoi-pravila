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
from svoi_pravila.domain.contact import Contact
from svoi_pravila.domain.enums import (
    RelationshipKind,
    RuleCategory,
    RuleStatus,
    SuggestionStatus,
    UsageScenario,
    UsageSurface,
    VariantKind,
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


async def _user_with_contact(world: AppWorld, telegram_id: int = 501) -> tuple[User, Contact]:
    user = await world.ensure_granted_user(telegram_id)
    contact = (
        await CreateContact(world.uow_factory, world.catalog, world.ids, world.clock).execute(
            CreateContactCommand(user.id, ContactLabel("Мама"), RelationshipKind.FAMILY)
        )
    ).contact
    return user, contact


async def _choose(
    world: AppWorld,
    telegram_id: int,
    variant_kind: VariantKind,
    contact_id: ContactId | None,
    surface: UsageSurface = UsageSurface.MINIAPP,
) -> RecordInlineChoiceResult:
    return await _recorder(world).execute(
        RecordInlineChoiceCommand(
            TelegramUserId(telegram_id),
            scenario=UsageScenario.REWRITE,
            kind=variant_kind,
            surface=surface,
            contact_id=contact_id,
        )
    )


@pytest.mark.unit
async def test_threshold_crossing_creates_exactly_one_suggestion(world: AppWorld) -> None:
    user, contact = await _user_with_contact(world, 510)
    for _ in range(4):
        result = await _choose(world, 510, VariantKind.SOFTER, contact.id)
        assert result.suggestion_id is None
        assert result.tone_outcome is ToneSignalOutcome.RECORDED
    result = await _choose(world, 510, VariantKind.SOFTER, contact.id)
    assert result.suggestion_id is not None
    assert result.tone_outcome is ToneSignalOutcome.SUGGESTION_CREATED
    listed = await ListSuggestions(world.uow_factory, world.catalog).execute(
        ListSuggestionsCommand(user.id, contact.id)
    )
    assert len(listed.suggestions) == 1
    assert listed.suggestions[0].variant_kind is VariantKind.SOFTER
    assert listed.suggestions[0].status is SuggestionStatus.PENDING


@pytest.mark.unit
async def test_check_choice_never_feeds_the_tone_signal(world: AppWorld) -> None:
    user, contact = await _user_with_contact(world, 509)
    for _ in range(6):
        result = await _choose(world, 509, VariantKind.CHECK, contact.id)
        assert result.suggestion_id is None
        assert result.tone_outcome is ToneSignalOutcome.SKIPPED_CHECK
    async with world.uow_factory() as uow:
        assert await uow.tone_signals.list_for_user(user.id) == []
        assert await uow.rule_suggestions.list_for_user(user.id) == []


@pytest.mark.unit
async def test_repeats_do_not_create_another_suggestion(world: AppWorld) -> None:
    _user, contact = await _user_with_contact(world, 511)
    for _ in range(5):
        await _choose(world, 511, VariantKind.FIRMER, contact.id)
    second = await _choose(world, 511, VariantKind.FIRMER, contact.id)
    assert second.suggestion_id is None
    assert second.tone_outcome is ToneSignalOutcome.RECORDED
    async with world.uow_factory() as uow:
        user = await uow.users.get_by_telegram_id(TelegramUserId(511))
        assert user is not None
        all_suggestions = await uow.rule_suggestions.list_for_user(user.id)
        assert len(all_suggestions) == 1


@pytest.mark.unit
async def test_pending_tone_does_not_freeze_signal_when_dominant_shifts(world: AppWorld) -> None:
    user, contact = await _user_with_contact(world, 522)
    for _ in range(5):
        await _choose(world, 522, VariantKind.SOFTER, contact.id)
    for index in range(10):
        result = await _choose(world, 522, VariantKind.FIRMER, contact.id)
        assert result.suggestion_id is None
        assert result.tone_outcome is ToneSignalOutcome.RECORDED
        async with world.uow_factory() as uow:
            signal = await uow.tone_signals.get(user.id, contact.id)
            assert signal is not None
            assert signal.values[-1] is VariantKind.FIRMER
            assert len(signal.values) == min(5 + index + 1, 10)
    listed = await ListSuggestions(world.uow_factory, world.catalog).execute(
        ListSuggestionsCommand(user.id, contact.id)
    )
    assert len(listed.suggestions) == 1
    assert listed.suggestions[0].variant_kind is VariantKind.SOFTER
    await DismissSuggestion(world.uow_factory, world.catalog, world.clock).execute(
        DismissSuggestionCommand(user.id, listed.suggestions[0].id)
    )
    created = await _choose(world, 522, VariantKind.FIRMER, contact.id)
    assert created.tone_outcome is ToneSignalOutcome.SUGGESTION_CREATED
    assert created.suggestion_id is not None
    after = await ListSuggestions(world.uow_factory, world.catalog).execute(
        ListSuggestionsCommand(user.id, contact.id)
    )
    assert len(after.suggestions) == 1
    assert after.suggestions[0].variant_kind is VariantKind.FIRMER


@pytest.mark.unit
async def test_dismissed_is_never_re_offered(world: AppWorld) -> None:
    user, contact = await _user_with_contact(world, 512)
    for _ in range(5):
        await _choose(world, 512, VariantKind.FIRMER, contact.id)
    listed = await ListSuggestions(world.uow_factory, world.catalog).execute(
        ListSuggestionsCommand(user.id, contact.id)
    )
    suggestion = listed.suggestions[0]
    dismissed = await DismissSuggestion(world.uow_factory, world.catalog, world.clock).execute(
        DismissSuggestionCommand(user.id, suggestion.id)
    )
    assert dismissed.outcome is DismissSuggestionOutcome.DISMISSED
    again = await _choose(world, 512, VariantKind.FIRMER, contact.id)
    assert again.suggestion_id is None
    listed_after = await ListSuggestions(world.uow_factory, world.catalog).execute(
        ListSuggestionsCommand(user.id, contact.id)
    )
    assert listed_after.suggestions == ()


@pytest.mark.unit
async def test_another_contact_is_independent(world: AppWorld) -> None:
    user, first = await _user_with_contact(world, 513)
    second = (
        await CreateContact(world.uow_factory, world.catalog, world.ids, world.clock).execute(
            CreateContactCommand(user.id, ContactLabel("Папа"), RelationshipKind.FAMILY)
        )
    ).contact
    for _ in range(5):
        await _choose(world, 513, VariantKind.SOFTER, first.id)
    for _ in range(4):
        result = await _choose(world, 513, VariantKind.SOFTER, second.id)
        assert result.suggestion_id is None
    result = await _choose(world, 513, VariantKind.SOFTER, second.id)
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
async def test_miniapp_without_contact_records_no_signal(world: AppWorld) -> None:
    user, _contact = await _user_with_contact(world, 514)
    result = await _choose(world, 514, VariantKind.SOFTER, None)
    assert result.suggestion_id is None
    assert result.tone_outcome is ToneSignalOutcome.SKIPPED_NO_CONTACT
    async with world.uow_factory() as uow:
        assert await uow.tone_signals.list_for_user(user.id) == []


@pytest.mark.unit
async def test_inline_choice_never_records_signal_even_with_contact(
    world: AppWorld,
) -> None:
    user, _contact = await _user_with_contact(world, 523)
    for _ in range(6):
        result = await _choose(world, 523, VariantKind.SOFTER, None, UsageSurface.INLINE)
        assert result.tone_outcome is ToneSignalOutcome.SKIPPED_INLINE
        assert result.suggestion_id is None
    inline_ref = await _recorder(world).execute(
        RecordInlineChoiceCommand(
            TelegramUserId(523), encode_inline_result_ref(UsageScenario.REWRITE, VariantKind.SOFTER)
        )
    )
    assert inline_ref.tone_outcome is ToneSignalOutcome.SKIPPED_INLINE
    async with world.uow_factory() as uow:
        assert await uow.tone_signals.list_for_user(user.id) == []
        assert await uow.rule_suggestions.list_for_user(user.id) == []


@pytest.mark.unit
async def test_inline_choice_ignores_contact_id_even_if_provided(world: AppWorld) -> None:
    user, contact = await _user_with_contact(world, 524)
    result = await _choose(world, 524, VariantKind.SOFTER, contact.id, UsageSurface.INLINE)
    assert result.tone_outcome is ToneSignalOutcome.SKIPPED_INLINE
    async with world.uow_factory() as uow:
        assert await uow.tone_signals.list_for_user(user.id) == []


@pytest.mark.unit
async def test_signal_attributed_to_bound_contact_only(world: AppWorld) -> None:
    user, bound = await _user_with_contact(world, 525)
    other = (
        await CreateContact(world.uow_factory, world.catalog, world.ids, world.clock).execute(
            CreateContactCommand(user.id, ContactLabel("Папа"), RelationshipKind.FAMILY)
        )
    ).contact
    result = await _choose(world, 525, VariantKind.FIRMER, bound.id)
    assert result.tone_outcome is ToneSignalOutcome.RECORDED
    async with world.uow_factory() as uow:
        assert await uow.tone_signals.get(user.id, bound.id) is not None
        assert await uow.tone_signals.get(user.id, other.id) is None


@pytest.mark.unit
async def test_stranger_cannot_attribute_signal_to_foreign_contact(world: AppWorld) -> None:
    owner, contact = await _user_with_contact(world, 526)
    stranger = await world.ensure_granted_user(527)
    stranger_result = await _choose(world, 527, VariantKind.SOFTER, contact.id)
    assert stranger_result.tone_outcome is ToneSignalOutcome.SKIPPED_NO_CONTACT
    owner_result = await _choose(world, 526, VariantKind.SOFTER, contact.id)
    assert owner_result.tone_outcome is ToneSignalOutcome.RECORDED
    async with world.uow_factory() as uow:
        assert await uow.tone_signals.list_for_user(stranger.id) == []
        assert len(await uow.tone_signals.list_for_user(owner.id)) == 1


@pytest.mark.unit
async def test_revoked_records_no_signal(world: AppWorld) -> None:
    user, contact = await _user_with_contact(world, 515)
    await RevokeAllConsents(world.uow_factory, world.clock, make_inline_reuse(world.clock)).execute(
        RevokeAllConsentsCommand(user.telegram_user_id)
    )
    result = await _choose(world, 515, VariantKind.SOFTER, contact.id)
    assert result.suggestion_id is None
    assert result.tone_outcome is ToneSignalOutcome.SKIPPED_NO_ACCESS
    async with world.uow_factory() as uow:
        signals = await uow.tone_signals.list_for_user(user.id)
        assert signals == []


@pytest.mark.unit
async def test_accept_creates_active_rule(world: AppWorld) -> None:
    user, contact = await _user_with_contact(world, 516)
    for _ in range(5):
        await _choose(world, 516, VariantKind.SOFTER, contact.id)
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
    user, contact = await _user_with_contact(world, 517)
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
        await _choose(world, 517, VariantKind.FIRMER, contact.id)
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
    user, contact = await _user_with_contact(world, 518)
    for _ in range(5):
        await _choose(world, 518, VariantKind.SOFTER, contact.id)
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
    user, _contact = await _user_with_contact(world, 519)
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
    _user, contact = await _user_with_contact(world, 521)
    monkeypatch.setattr(InMemoryToneSignalRepository, "upsert", _boom_upsert)
    result = await _choose(world, 521, VariantKind.SOFTER, contact.id)
    assert result.suggestion_id is None
    assert result.tone_outcome is ToneSignalOutcome.SKIPPED_CONFLICT

    result = await _choose(world, 521, VariantKind.SOFTER, ContactId(UUID(int=404)))
    assert result.suggestion_id is None
    assert result.tone_outcome is ToneSignalOutcome.SKIPPED_NO_CONTACT


@pytest.mark.unit
async def test_unknown_user_with_bound_contact_skips(world: AppWorld) -> None:
    result = await _choose(world, 999, VariantKind.SOFTER, ContactId(UUID(int=1)))
    assert result.tone_outcome is ToneSignalOutcome.SKIPPED_NO_CONTACT
