"""Revoke-all, leave-pair, delete-account, and export use cases."""

from __future__ import annotations

from uuid import UUID

import pytest

from svoi_pravila.application.errors import NotFound, OpenRuleLimitReached
from svoi_pravila.application.use_cases.approve_rule import ApproveRule, ApproveRuleCommand
from svoi_pravila.application.use_cases.delete_my_account import (
    DeleteMyAccount,
    DeleteMyAccountCommand,
    DeleteMyAccountPorts,
)
from svoi_pravila.application.use_cases.export_my_data import ExportMyData, ExportMyDataCommand
from svoi_pravila.application.use_cases.get_onboarding_step import (
    GetOnboardingStep,
    GetOnboardingStepQuery,
    OnboardingStepKind,
)
from svoi_pravila.application.use_cases.leave_pair import LeavePair, LeavePairCommand
from svoi_pravila.application.use_cases.propose_rule import ProposeRule, ProposeRuleCommand
from svoi_pravila.application.use_cases.revoke_all_consents import (
    RevokeAllConsents,
    RevokeAllConsentsCommand,
)
from svoi_pravila.domain.contact import Contact
from svoi_pravila.domain.enums import (
    ConsentKind,
    Firmness,
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
    RuleSuggestionId,
    TelegramUserId,
    UsageEventId,
    UserId,
)
from svoi_pravila.domain.pair import Pair
from svoi_pravila.domain.rule_suggestion import RuleSuggestion, ToneSignal
from svoi_pravila.domain.rules import MAX_OPEN_RULES_PER_SCOPE, ContactScope, PairScope
from svoi_pravila.domain.text import ContactLabel, RuleText
from svoi_pravila.domain.usage import UsageEvent
from tests.fakes.inline_reuse import make_inline_reuse
from tests.fakes.rate_limit import FakePseudonymizer
from tests.unit.application.conftest import AppWorld
from tests.unit.application.test_rules_and_invites import _pair_world


@pytest.mark.unit
async def test_revoke_all_unknown_and_idempotent(world: AppWorld) -> None:
    reuse = make_inline_reuse(world.clock)
    missing = await RevokeAllConsents(world.uow_factory, world.clock, reuse).execute(
        RevokeAllConsentsCommand(TelegramUserId(999001))
    )
    assert missing.found is False
    user = await world.ensure_granted_user(40)
    first = await RevokeAllConsents(world.uow_factory, world.clock, reuse).execute(
        RevokeAllConsentsCommand(user.telegram_user_id)
    )
    assert first.found is True
    assert len(first.consents) == 2
    second = await RevokeAllConsents(world.uow_factory, world.clock, reuse).execute(
        RevokeAllConsentsCommand(user.telegram_user_id)
    )
    assert second.found is True
    assert second.consents == ()
    step = await GetOnboardingStep(world.uow_factory, world.catalog).execute(
        GetOnboardingStepQuery(user.telegram_user_id)
    )
    assert step.step.kind is OnboardingStepKind.CONSENT
    assert step.step.consent_kind is ConsentKind.PERSONAL_DATA


@pytest.mark.unit
async def test_leave_pair_rehomes_remaining_authored_rules(world: AppWorld) -> None:
    inviter, invitee, contact, accepted = await _pair_world(world)
    private = await ProposeRule(
        world.uow_factory, world.catalog, world.ids, world.clock, world.notifier
    ).execute(
        ProposeRuleCommand(
            inviter.id,
            contact.id,
            RuleCategory.OTHER,
            RuleText("inviter private"),
            shared=False,
        )
    )
    shared = await ProposeRule(
        world.uow_factory, world.catalog, world.ids, world.clock, world.notifier
    ).execute(
        ProposeRuleCommand(
            inviter.id,
            contact.id,
            RuleCategory.OTHER,
            RuleText("inviter shared"),
            shared=True,
        )
    )
    await ApproveRule(world.uow_factory, world.catalog, world.clock, world.notifier).execute(
        ApproveRuleCommand(invitee.id, shared.rule.id)
    )
    invitee_shared = await ProposeRule(
        world.uow_factory, world.catalog, world.ids, world.clock, world.notifier
    ).execute(
        ProposeRuleCommand(
            invitee.id,
            accepted.invitee_contact.id,
            RuleCategory.OTHER,
            RuleText("invitee shared"),
            shared=True,
        )
    )
    await ApproveRule(world.uow_factory, world.catalog, world.clock, world.notifier).execute(
        ApproveRuleCommand(inviter.id, invitee_shared.rule.id)
    )
    await LeavePair(world.uow_factory, world.ids, world.clock, world.notifier).execute(
        LeavePairCommand(inviter.id, accepted.pair.id)
    )
    async with world.uow_factory() as uow:
        assert await uow.pairs.get(accepted.pair.id) is None
        remaining = await uow.contacts.get(accepted.invitee_contact.id)
        assert remaining is not None
        assert remaining.pair_id is None
        leaver = await uow.contacts.get(contact.id)
        assert leaver is not None
        assert leaver.pair_id is None
        private_kept = await uow.rules.get(private.rule.id)
        assert private_kept is not None
        rehomed = await uow.rules.list_for_scope(ContactScope(contact_id=remaining.id))
        texts = {rule.revisions[-1].text.value for rule in rehomed}
        assert "invitee shared" in texts
        assert "inviter shared" not in texts
        assert await uow.rules.list_for_scope(PairScope(pair_id=accepted.pair.id)) == []


@pytest.mark.unit
async def test_leave_pair_not_found_and_open_limit(world: AppWorld) -> None:
    with pytest.raises(NotFound):
        await LeavePair(world.uow_factory, world.ids, world.clock, world.notifier).execute(
            LeavePairCommand(UserId(UUID(int=1)), PairId(UUID(int=2)))
        )
    inviter, invitee, _contact, accepted = await _pair_world(world)
    shared = await ProposeRule(
        world.uow_factory, world.catalog, world.ids, world.clock, world.notifier
    ).execute(
        ProposeRuleCommand(
            invitee.id,
            accepted.invitee_contact.id,
            RuleCategory.OTHER,
            RuleText("one more open"),
            shared=True,
        )
    )
    await ApproveRule(world.uow_factory, world.catalog, world.clock, world.notifier).execute(
        ApproveRuleCommand(inviter.id, shared.rule.id)
    )
    for index in range(MAX_OPEN_RULES_PER_SCOPE):
        await ProposeRule(
            world.uow_factory, world.catalog, world.ids, world.clock, world.notifier
        ).execute(
            ProposeRuleCommand(
                invitee.id,
                accepted.invitee_contact.id,
                RuleCategory.OTHER,
                RuleText(f"fill {index}"),
                shared=False,
            )
        )
    with pytest.raises(OpenRuleLimitReached):
        await LeavePair(world.uow_factory, world.ids, world.clock, world.notifier).execute(
            LeavePairCommand(inviter.id, accepted.pair.id)
        )


@pytest.mark.unit
async def test_leave_pair_not_member_and_missing_remaining_contact(world: AppWorld) -> None:
    inviter = await world.ensure_granted_user(70)
    stranger = await world.ensure_granted_user(71)
    pair = Pair(
        id=PairId(UUID(int=80)),
        members=frozenset({inviter.id, stranger.id}),
        created_at=world.clock.now(),
    )
    async with world.uow_factory() as uow:
        await uow.pairs.add(pair)
        await uow.commit()
    with pytest.raises(NotFound):
        await LeavePair(world.uow_factory, world.ids, world.clock, world.notifier).execute(
            LeavePairCommand(inviter.id, pair.id)
        )
    outsider = await world.ensure_granted_user(72)
    with pytest.raises(NotFound):
        await LeavePair(world.uow_factory, world.ids, world.clock, world.notifier).execute(
            LeavePairCommand(outsider.id, pair.id)
        )
    async with world.uow_factory() as uow:
        remaining = await uow.contacts.get_for_owner_and_pair(stranger.id, pair.id)
        assert remaining is None
        contact = Contact(
            id=ContactId(UUID(int=81)),
            owner_id=stranger.id,
            label=ContactLabel("only remaining"),
            relationship=RelationshipKind.FRIEND,
            pair_id=pair.id,
            created_at=world.clock.now(),
        )
        await uow.contacts.add(contact)
        await uow.commit()
    await LeavePair(world.uow_factory, world.ids, world.clock, world.notifier).execute(
        LeavePairCommand(inviter.id, pair.id)
    )


@pytest.mark.unit
async def test_delete_and_export(world: AppWorld) -> None:
    inviter, invitee, contact, accepted = await _pair_world(world)
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
    partner_private = await ProposeRule(
        world.uow_factory, world.catalog, world.ids, world.clock, world.notifier
    ).execute(
        ProposeRuleCommand(
            invitee.id,
            accepted.invitee_contact.id,
            RuleCategory.OTHER,
            RuleText("partner only"),
            shared=False,
        )
    )
    shared = await ProposeRule(
        world.uow_factory, world.catalog, world.ids, world.clock, world.notifier
    ).execute(
        ProposeRuleCommand(
            inviter.id,
            contact.id,
            RuleCategory.OTHER,
            RuleText("together"),
            shared=True,
        )
    )
    await ApproveRule(world.uow_factory, world.catalog, world.clock, world.notifier).execute(
        ApproveRuleCommand(invitee.id, shared.rule.id)
    )
    pseudo = FakePseudonymizer()
    async with world.uow_factory() as uow:
        stored = await uow.users.get(inviter.id)
        assert stored is not None
        await uow.users.update(stored.set_active_contact(contact.id))
        await uow.commit()
    analytics_a = pseudo.pseudonymize("analytics", str(inviter.telegram_user_id.value))
    analytics_b = pseudo.pseudonymize("analytics", str(invitee.telegram_user_id.value))
    async with world.uow_factory() as uow:
        await uow.rule_suggestions.add(
            RuleSuggestion.create_tone(
                suggestion_id=RuleSuggestionId(UUID(int=95)),
                user_id=inviter.id,
                contact_id=contact.id,
                category=RuleCategory.HOW_TO_ASK,
                text=RuleText("Говорить мягко, без резких формулировок"),
                firmness=Firmness.GENTLE,
                now=world.clock.now(),
            )
        )
        await uow.tone_signals.upsert(
            ToneSignal(
                user_id=inviter.id,
                contact_id=contact.id,
                values=(Firmness.GENTLE,) * 5,
            )
        )
        await uow.usage_events.add(
            UsageEvent(
                id=UsageEventId(UUID(int=90)),
                occurred_at=world.clock.now(),
                user_pseudonym=analytics_a,
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
        )
        await uow.usage_events.add(
            UsageEvent(
                id=UsageEventId(UUID(int=91)),
                occurred_at=world.clock.now(),
                user_pseudonym=analytics_b,
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
        )
        await uow.commit()

    unknown_export = await ExportMyData(world.uow_factory, world.clock).execute(
        ExportMyDataCommand(TelegramUserId(888))
    )
    assert unknown_export.found is False
    dumped = await ExportMyData(world.uow_factory, world.clock).execute(
        ExportMyDataCommand(inviter.telegram_user_id)
    )
    assert dumped.found is True
    assert dumped.payload is not None
    assert dumped.payload["export_version"] == 1
    blob = str(dumped.payload)
    assert "secret private" in blob
    assert "together" in blob
    assert "partner only" not in blob
    assert "Говорить мягко, без резких формулировок" in blob
    assert "gentle" in blob

    deleter = DeleteMyAccount(
        DeleteMyAccountPorts(
            world.uow_factory,
            world.ids,
            pseudo,
            world.clock,
            make_inline_reuse(world.clock),
            world.notifier,
        )
    )
    missing = await deleter.execute(DeleteMyAccountCommand(TelegramUserId(777)))
    assert missing.found is False
    gone = await deleter.execute(DeleteMyAccountCommand(inviter.telegram_user_id))
    assert gone.found is True
    async with world.uow_factory() as uow:
        assert await uow.users.get(inviter.id) is None
        assert await uow.users.get(invitee.id) is not None
        assert await uow.usage_events.get(UsageEventId(UUID(int=90))) is None
        assert await uow.usage_events.get(UsageEventId(UUID(int=91))) is not None
        assert await uow.rule_suggestions.list_for_user(inviter.id) == []
        assert await uow.tone_signals.list_for_user(inviter.id) == []
        remaining_rules = await uow.rules.list_for_scope(
            ContactScope(contact_id=accepted.invitee_contact.id)
        )
        texts = {rule.revisions[-1].text.value for rule in remaining_rules}
        assert "partner only" in texts
        assert partner_private.rule.id in {rule.id for rule in remaining_rules}
        statuses = {rule.status for rule in remaining_rules}
        assert RuleStatus.ACTIVE in statuses


@pytest.mark.unit
async def test_dissolve_pair_guards_and_delete_without_partner_contact(
    world: AppWorld,
) -> None:
    from svoi_pravila.application.use_cases._leave_pair import dissolve_pair_for_leaving_member
    from svoi_pravila.domain.rules import PairScope, Rule

    inviter = await world.ensure_granted_user(76)
    stranger = await world.ensure_granted_user(77)
    outsider = await world.ensure_granted_user(78)
    pair = Pair(
        id=PairId(UUID(int=90)),
        members=frozenset({inviter.id, stranger.id}),
        created_at=world.clock.now(),
    )
    leaving = Contact(
        id=ContactId(UUID(int=92)),
        owner_id=inviter.id,
        label=ContactLabel("orphan leave"),
        relationship=RelationshipKind.FRIEND,
        pair_id=pair.id,
        created_at=world.clock.now(),
    )
    orphan_rule = Rule.propose(
        rule_id=RuleId(UUID(int=93)),
        scope=PairScope(pair_id=pair.id),
        category=RuleCategory.OTHER,
        approvers=frozenset({inviter.id, stranger.id}),
        author_id=inviter.id,
        text=RuleText("orphan shared"),
        now=world.clock.now(),
    )
    async with world.uow_factory() as uow:
        await uow.pairs.add(pair)
        await uow.contacts.add(leaving)
        await uow.rules.add(orphan_rule)
        await uow.commit()
        with pytest.raises(NotFound):
            await dissolve_pair_for_leaving_member(
                uow,
                world.ids,
                actor_id=outsider.id,
                pair=pair,
                now=world.clock.now(),
            )
        await dissolve_pair_for_leaving_member(
            uow,
            world.ids,
            actor_id=inviter.id,
            pair=pair,
            now=world.clock.now(),
        )
        await uow.commit()
        assert await uow.pairs.get(pair.id) is None
        assert await uow.rules.get(orphan_rule.id) is None
        stored_leaving = await uow.contacts.get(leaving.id)
        assert stored_leaving is not None
        assert stored_leaving.pair_id is None

    orphan = Pair(
        id=PairId(UUID(int=91)),
        members=frozenset({inviter.id, stranger.id}),
        created_at=world.clock.now(),
    )
    async with world.uow_factory() as uow:
        await uow.pairs.add(orphan)
        await uow.commit()

    deleter = DeleteMyAccount(
        DeleteMyAccountPorts(
            world.uow_factory,
            world.ids,
            FakePseudonymizer(),
            world.clock,
            make_inline_reuse(world.clock),
            world.notifier,
        )
    )
    gone = await deleter.execute(DeleteMyAccountCommand(inviter.telegram_user_id))
    assert gone.found is True
    assert world.notifier.partner_left_calls == []
