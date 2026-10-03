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


TestRuleMachine = pytest.mark.unit(RuleMachine.TestCase)
