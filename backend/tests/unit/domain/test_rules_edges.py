"""Edge-case coverage for Rule aggregate validation and transitions."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from svoi_pravila.domain.enums import RuleCategory, RuleStatus
from svoi_pravila.domain.errors import InvalidTransitionError, InvalidValueError
from svoi_pravila.domain.ids import ContactId, RuleId, UserId
from svoi_pravila.domain.rules import ContactScope, Rule, RuleRevision
from svoi_pravila.domain.text import RuleText

NOW = datetime(2026, 1, 1, tzinfo=UTC)
OWNER = UserId(UUID(int=1))
PARTNER = UserId(UUID(int=2))
STRANGER = UserId(UUID(int=3))


@pytest.mark.unit
def test_revision_and_rule_validation() -> None:
    with pytest.raises(InvalidValueError):
        RuleRevision(
            number=0,
            text=RuleText("x"),
            author_id=OWNER,
            proposed_at=NOW,
            approved_by=frozenset({OWNER}),
            effective_since=None,
        )
    with pytest.raises(InvalidValueError):
        RuleRevision(
            number=1,
            text=RuleText("x"),
            author_id=OWNER,
            proposed_at=NOW,
            approved_by=frozenset({OWNER}),
            effective_since=NOW - timedelta(seconds=1),
        )
    with pytest.raises(InvalidValueError):
        Rule.propose(
            rule_id=RuleId(UUID(int=1)),
            scope=ContactScope(ContactId(UUID(int=2))),
            category=RuleCategory.OTHER,
            approvers=frozenset(),
            author_id=OWNER,
            text=RuleText("x"),
            now=NOW,
        )
    with pytest.raises(InvalidTransitionError):
        Rule.propose(
            rule_id=RuleId(UUID(int=2)),
            scope=ContactScope(ContactId(UUID(int=2))),
            category=RuleCategory.OTHER,
            approvers=frozenset({OWNER}),
            author_id=PARTNER,
            text=RuleText("x"),
            now=NOW,
        )
    proposed = Rule.propose(
        rule_id=RuleId(UUID(int=3)),
        scope=ContactScope(ContactId(UUID(int=2))),
        category=RuleCategory.OTHER,
        approvers=frozenset({OWNER, PARTNER}),
        author_id=OWNER,
        text=RuleText("x"),
        now=NOW,
    )
    assert proposed.effective_revision is None
    assert proposed.pending_revision is not None


@pytest.mark.unit
def test_transition_errors() -> None:
    private = Rule.propose(
        rule_id=RuleId(UUID(int=4)),
        scope=ContactScope(ContactId(UUID(int=2))),
        category=RuleCategory.OTHER,
        approvers=frozenset({OWNER}),
        author_id=OWNER,
        text=RuleText("x"),
        now=NOW,
    )
    with pytest.raises(InvalidTransitionError):
        private.approve(OWNER, NOW)
    with pytest.raises(InvalidTransitionError):
        private.approve(PARTNER, NOW)
    with pytest.raises(InvalidTransitionError):
        private.propose_edit(PARTNER, RuleText("y"), NOW)

    pairish = Rule.propose(
        rule_id=RuleId(UUID(int=5)),
        scope=ContactScope(ContactId(UUID(int=2))),
        category=RuleCategory.OTHER,
        approvers=frozenset({OWNER, PARTNER}),
        author_id=OWNER,
        text=RuleText("x"),
        now=NOW,
    )
    with pytest.raises(InvalidTransitionError):
        pairish.reject_pending(OWNER, NOW)
    with pytest.raises(InvalidTransitionError):
        pairish.reject_pending(STRANGER, NOW)
    rejected = pairish.reject_pending(PARTNER, NOW)
    assert rejected.status is RuleStatus.REJECTED
    with pytest.raises(InvalidTransitionError):
        rejected.reject_pending(PARTNER, NOW)
    with pytest.raises(InvalidTransitionError):
        rejected.archive(OWNER, NOW)
    with pytest.raises(InvalidTransitionError):
        private.archive(PARTNER, NOW)

    active_pair = Rule.propose(
        rule_id=RuleId(UUID(int=6)),
        scope=ContactScope(ContactId(UUID(int=2))),
        category=RuleCategory.OTHER,
        approvers=frozenset({OWNER, PARTNER}),
        author_id=OWNER,
        text=RuleText("x"),
        now=NOW,
    ).approve(PARTNER, NOW)
    pending = active_pair.propose_edit(OWNER, RuleText("y"), NOW)
    with pytest.raises(InvalidTransitionError):
        pending.propose_edit(OWNER, RuleText("z"), NOW)
    archived = pending.archive(PARTNER, NOW)
    with pytest.raises(InvalidTransitionError):
        archived.approve(OWNER, NOW)
