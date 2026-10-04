"""Rule aggregate tests and Hypothesis stateful machine."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, initialize, invariant, rule

from svoi_pravila.domain.enums import RuleCategory, RuleStatus
from svoi_pravila.domain.errors import InvalidTransitionError, InvalidValueError
from svoi_pravila.domain.ids import ContactId, PairId, RuleId, UserId
from svoi_pravila.domain.rules import ContactScope, PairScope, Rule
from svoi_pravila.domain.text import RuleText

NOW = datetime(2026, 1, 1, tzinfo=UTC)
OWNER = UserId(UUID(int=1))
PARTNER = UserId(UUID(int=2))
STRANGER = UserId(UUID(int=3))


def _reconstruct(rule: Rule) -> Rule:
    return Rule(
        id=rule.id,
        scope=rule.scope,
        category=rule.category,
        approvers=rule.approvers,
        status=rule.status,
        revisions=rule.revisions,
        created_at=rule.created_at,
    )


@pytest.mark.unit
def test_private_propose_becomes_active() -> None:
    rule = Rule.propose(
        rule_id=RuleId(UUID(int=10)),
        scope=ContactScope(contact_id=ContactId(UUID(int=20))),
        category=RuleCategory.OTHER,
        approvers=frozenset({OWNER}),
        author_id=OWNER,
        text=RuleText("do not joke about X"),
        now=NOW,
    )
    assert rule.status is RuleStatus.ACTIVE
    assert _reconstruct(rule) == rule


@pytest.mark.unit
def test_pair_propose_requires_both() -> None:
    rule = Rule.propose(
        rule_id=RuleId(UUID(int=11)),
        scope=PairScope(pair_id=PairId(UUID(int=21))),
        category=RuleCategory.TABOO_TOPIC,
        approvers=frozenset({OWNER, PARTNER}),
        author_id=OWNER,
        text=RuleText("ask gently"),
        now=NOW,
    )
    assert rule.status is RuleStatus.PROPOSED
    approved = rule.approve(PARTNER, NOW + timedelta(seconds=1))
    assert approved.status is RuleStatus.ACTIVE
    assert _reconstruct(approved) == approved


@pytest.mark.unit
def test_approve_rejected_is_forbidden() -> None:
    rule = Rule.propose(
        rule_id=RuleId(UUID(int=12)),
        scope=PairScope(pair_id=PairId(UUID(int=22))),
        category=RuleCategory.APOLOGY,
        approvers=frozenset({OWNER, PARTNER}),
        author_id=OWNER,
        text=RuleText("first"),
        now=NOW,
    )
    rejected = rule.reject_pending(PARTNER, NOW)
    with pytest.raises(InvalidTransitionError):
        rejected.approve(PARTNER, NOW + timedelta(seconds=1))


@pytest.mark.unit
def test_reject_edit_keeps_effective() -> None:
    active = Rule.propose(
        rule_id=RuleId(UUID(int=13)),
        scope=PairScope(pair_id=PairId(UUID(int=22))),
        category=RuleCategory.APOLOGY,
        approvers=frozenset({OWNER, PARTNER}),
        author_id=OWNER,
        text=RuleText("first"),
        now=NOW,
    ).approve(PARTNER, NOW)
    edited = active.propose_edit(OWNER, RuleText("second"), NOW + timedelta(seconds=1))
    assert edited.effective_revision is not None
    assert edited.effective_revision.text.value == "first"
    rolled = edited.reject_pending(PARTNER, NOW + timedelta(seconds=2))
    assert rolled.effective_revision is not None
    assert rolled.effective_revision.text.value == "first"
    assert _reconstruct(rolled) == rolled


@pytest.mark.unit
def test_approve_edit_stays_active() -> None:
    active = Rule.propose(
        rule_id=RuleId(UUID(int=14)),
        scope=PairScope(pair_id=PairId(UUID(int=23))),
        category=RuleCategory.APOLOGY,
        approvers=frozenset({OWNER, PARTNER}),
        author_id=OWNER,
        text=RuleText("first"),
        now=NOW,
    ).approve(PARTNER, NOW)
    edited = active.propose_edit(OWNER, RuleText("second"), NOW + timedelta(seconds=1))
    approved = edited.approve(PARTNER, NOW + timedelta(seconds=2))
    assert approved.status is RuleStatus.ACTIVE
    assert approved.effective_revision is not None
    assert approved.effective_revision.text.value == "second"
    assert _reconstruct(approved) == approved


@pytest.mark.unit
def test_invalid_rehydration_rejected() -> None:
    with pytest.raises(InvalidValueError):
        Rule(
            id=RuleId(UUID(int=99)),
            scope=ContactScope(ContactId(UUID(int=1))),
            category=RuleCategory.OTHER,
            approvers=frozenset({OWNER}),
            status=RuleStatus.ACTIVE,
            revisions=(),
            created_at=NOW,
        )


class RuleMachine(RuleBasedStateMachine):
    """Random approve/edit/reject/archive sequences preserve invariants."""

    def __init__(self) -> None:
        super().__init__()
        self.rule: Rule | None = None
        self.t = NOW

    def _set(self, rule: Rule) -> None:
        self.rule = rule
        assert _reconstruct(rule) == rule

    @initialize()
    def start(self) -> None:
        self._set(
            Rule.propose(
                rule_id=RuleId(UUID(int=100)),
                scope=PairScope(pair_id=PairId(UUID(int=200))),
                category=RuleCategory.CONFLICT_PROTOCOL,
                approvers=frozenset({OWNER, PARTNER}),
                author_id=OWNER,
                text=RuleText("base"),
                now=self.t,
            )
        )
        self.t += timedelta(seconds=1)

    @rule(actor=st.sampled_from([OWNER, PARTNER]))
    def approve(self, actor: UserId) -> None:
        assert self.rule is not None
        try:
            self._set(self.rule.approve(actor, self.t))
        except InvalidTransitionError:
            return
        finally:
            self.t += timedelta(seconds=1)

    @rule(actor=st.sampled_from([OWNER, PARTNER]), text=st.sampled_from(["a", "b", "c"]))
    def edit(self, actor: UserId, text: str) -> None:
        assert self.rule is not None
        try:
            self._set(self.rule.propose_edit(actor, RuleText(text), self.t))
        except InvalidTransitionError:
            return
        finally:
            self.t += timedelta(seconds=1)

    @rule(actor=st.sampled_from([OWNER, PARTNER]))
    def reject(self, actor: UserId) -> None:
        assert self.rule is not None
        try:
            self._set(self.rule.reject_pending(actor, self.t))
        except InvalidTransitionError:
            return
        finally:
            self.t += timedelta(seconds=1)

    @rule(actor=st.sampled_from([OWNER, PARTNER]))
    def archive(self, actor: UserId) -> None:
        assert self.rule is not None
        try:
            self._set(self.rule.archive(actor, self.t))
        except InvalidTransitionError:
            return
        finally:
            self.t += timedelta(seconds=1)

    @invariant()
    def invariants(self) -> None:
        assert self.rule is not None
        assert _reconstruct(self.rule) == self.rule


@pytest.mark.unit
def test_rehome_authored_to_contact_copies_remaining_revisions() -> None:
    contact = ContactId(UUID(int=20))
    pair_rule = Rule.propose(
        rule_id=RuleId(UUID(int=10)),
        scope=PairScope(pair_id=PairId(UUID(int=30))),
        category=RuleCategory.OTHER,
        approvers=frozenset({OWNER, PARTNER}),
        author_id=OWNER,
        text=RuleText("owner text"),
        now=NOW,
    )
    pair_rule = pair_rule.approve(PARTNER, NOW)
    pair_rule = pair_rule.propose_edit(
        PARTNER, RuleText("partner edit"), NOW + timedelta(seconds=1)
    )
    copied = Rule.rehome_authored_to_contact(
        pair_rule,
        remaining_id=PARTNER,
        contact_id=contact,
        new_id=RuleId(UUID(int=99)),
        now=NOW + timedelta(days=1),
    )
    assert copied is not None
    assert copied.status is RuleStatus.ACTIVE
    assert copied.scope == ContactScope(contact_id=contact)
    assert copied.revisions[0].text.value == "partner edit"
    assert copied.revisions[0].effective_since == NOW + timedelta(days=1)
    assert copied.approvers == frozenset({PARTNER})
    missing = Rule.rehome_authored_to_contact(
        pair_rule,
        remaining_id=STRANGER,
        contact_id=contact,
        new_id=RuleId(UUID(int=100)),
        now=NOW + timedelta(days=1),
    )
    assert missing is None


@pytest.mark.unit
def test_rehome_rejected_and_archived() -> None:
    contact = ContactId(UUID(int=21))
    rejected = Rule.propose(
        rule_id=RuleId(UUID(int=11)),
        scope=PairScope(pair_id=PairId(UUID(int=31))),
        category=RuleCategory.OTHER,
        approvers=frozenset({OWNER, PARTNER}),
        author_id=OWNER,
        text=RuleText("pending"),
        now=NOW,
    )
    rejected = rejected.reject_pending(PARTNER, NOW)
    copied_rejected = Rule.rehome_authored_to_contact(
        rejected,
        remaining_id=OWNER,
        contact_id=contact,
        new_id=RuleId(UUID(int=101)),
        now=NOW + timedelta(days=1),
    )
    assert copied_rejected is not None
    assert copied_rejected.status is RuleStatus.ACTIVE

    active = Rule.propose(
        rule_id=RuleId(UUID(int=12)),
        scope=PairScope(pair_id=PairId(UUID(int=32))),
        category=RuleCategory.OTHER,
        approvers=frozenset({OWNER, PARTNER}),
        author_id=OWNER,
        text=RuleText("shared"),
        now=NOW,
    )
    active = active.approve(PARTNER, NOW)
    archived = active.archive(OWNER, NOW)
    copied_archived = Rule.rehome_authored_to_contact(
        archived,
        remaining_id=OWNER,
        contact_id=contact,
        new_id=RuleId(UUID(int=102)),
        now=NOW + timedelta(days=1),
    )
    assert copied_archived is not None
    assert copied_archived.status is RuleStatus.ARCHIVED
    assert copied_archived.revisions[0].effective_since == NOW


@pytest.mark.unit
def test_rehome_pending_takes_leave_now_effective_keeps_date() -> None:
    contact = ContactId(UUID(int=22))
    leave = NOW + timedelta(days=2)
    pending = Rule.propose(
        rule_id=RuleId(UUID(int=13)),
        scope=PairScope(pair_id=PairId(UUID(int=33))),
        category=RuleCategory.OTHER,
        approvers=frozenset({OWNER, PARTNER}),
        author_id=OWNER,
        text=RuleText("waiting"),
        now=NOW,
    )
    copied_pending = Rule.rehome_authored_to_contact(
        pending,
        remaining_id=OWNER,
        contact_id=contact,
        new_id=RuleId(UUID(int=103)),
        now=leave,
    )
    assert copied_pending is not None
    assert copied_pending.revisions[0].effective_since == leave
    assert copied_pending.revisions[0].effective_since != pending.revisions[0].proposed_at

    effective = pending.approve(PARTNER, NOW)
    copied_effective = Rule.rehome_authored_to_contact(
        effective,
        remaining_id=OWNER,
        contact_id=contact,
        new_id=RuleId(UUID(int=104)),
        now=leave,
    )
    assert copied_effective is not None
    assert copied_effective.revisions[0].effective_since == NOW


@pytest.mark.unit
def test_rehome_mixed_history_preserves_order_and_dates() -> None:
    contact = ContactId(UUID(int=23))
    leave = NOW + timedelta(days=3)
    edit_at = NOW + timedelta(hours=1)
    mixed = Rule.propose(
        rule_id=RuleId(UUID(int=14)),
        scope=PairScope(pair_id=PairId(UUID(int=34))),
        category=RuleCategory.OTHER,
        approvers=frozenset({OWNER, PARTNER}),
        author_id=OWNER,
        text=RuleText("agreed"),
        now=NOW,
    )
    mixed = mixed.approve(PARTNER, NOW)
    mixed = mixed.propose_edit(OWNER, RuleText("pending edit"), edit_at)
    copied = Rule.rehome_authored_to_contact(
        mixed,
        remaining_id=OWNER,
        contact_id=contact,
        new_id=RuleId(UUID(int=105)),
        now=leave,
    )
    assert copied is not None
    assert [rev.number for rev in copied.revisions] == [1, 2]
    assert copied.revisions[0].text.value == "agreed"
    assert copied.revisions[0].effective_since == NOW
    assert copied.revisions[1].text.value == "pending edit"
    assert copied.revisions[1].effective_since == leave
    assert copied.revisions[1].proposed_at == edit_at


TestRuleMachine = pytest.mark.unit(RuleMachine.TestCase)
