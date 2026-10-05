"""Unit tests for shared rules-list projection."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from svoi_pravila.application.rule_view import project_rules_for_list
from svoi_pravila.domain.enums import RuleCategory, RuleStatus
from svoi_pravila.domain.ids import ContactId, PairId, RuleId, UserId
from svoi_pravila.domain.rules import ContactScope, PairScope, Rule
from svoi_pravila.domain.text import RuleText

_OWNER = UserId(UUID(int=1))
_PARTNER = UserId(UUID(int=2))
_NOW = datetime(2026, 10, 3, 12, tzinfo=UTC)


def _private_active(*, rule_id: int = 10, text: str = "active text") -> Rule:
    return Rule.propose(
        rule_id=RuleId(UUID(int=rule_id)),
        scope=ContactScope(contact_id=ContactId(UUID(int=20))),
        category=RuleCategory.OTHER,
        approvers=frozenset({_OWNER}),
        author_id=_OWNER,
        text=RuleText(text),
        now=_NOW,
    )


def _pair_active(*, rule_id: int = 30, text: str = "shared active") -> Rule:
    return Rule.propose(
        rule_id=RuleId(UUID(int=rule_id)),
        scope=PairScope(pair_id=PairId(UUID(int=21))),
        category=RuleCategory.APOLOGY,
        approvers=frozenset({_OWNER, _PARTNER}),
        author_id=_OWNER,
        text=RuleText(text),
        now=_NOW,
    ).approve(_PARTNER, _NOW)


@pytest.mark.unit
def test_project_drops_archived_and_rejected() -> None:
    active = _private_active()
    proposed = Rule.propose(
        rule_id=RuleId(UUID(int=11)),
        scope=PairScope(pair_id=PairId(UUID(int=21))),
        category=RuleCategory.TABOO_TOPIC,
        approvers=frozenset({_OWNER, _PARTNER}),
        author_id=_OWNER,
        text=RuleText("pending text"),
        now=_NOW,
    )
    archived = active.archive(_OWNER, _NOW + timedelta(seconds=1))
    rejected = proposed.reject_pending(_PARTNER, _NOW + timedelta(seconds=1))
    views = project_rules_for_list((active, proposed, archived, rejected))
    assert [view.status for view in views] == [RuleStatus.ACTIVE, RuleStatus.PROPOSED]
    assert [view.text.value for view in views] == ["active text", "pending text"]


@pytest.mark.unit
def test_active_with_pending_edit_keeps_effective_text() -> None:
    active = _pair_active(text="effective body")
    edited = active.propose_edit(_OWNER, RuleText("pending edit body"), _NOW + timedelta(seconds=1))
    views = project_rules_for_list((edited,))
    assert len(views) == 1
    view = views[0]
    assert view.status is RuleStatus.ACTIVE
    assert view.text.value == "effective body"
    assert view.effective_since == _NOW
    assert view.has_pending_edit is True
    assert view.shared is True


@pytest.mark.unit
def test_bot_and_api_share_identical_views() -> None:
    """Same Rule fixtures project to identical views for bot and API call sites."""
    active = _private_active()
    with_pending = _pair_active(rule_id=31, text="active text").propose_edit(
        _OWNER, RuleText("edit in flight"), _NOW + timedelta(seconds=1)
    )
    proposed = Rule.propose(
        rule_id=RuleId(UUID(int=12)),
        scope=PairScope(pair_id=PairId(UUID(int=22))),
        category=RuleCategory.APOLOGY,
        approvers=frozenset({_OWNER, _PARTNER}),
        author_id=_OWNER,
        text=RuleText("pair pending"),
        now=_NOW,
    )
    rules = (with_pending, proposed, active.archive(_OWNER, _NOW + timedelta(seconds=2)))
    bot_views = project_rules_for_list(rules)
    api_views = project_rules_for_list(rules)
    assert bot_views == api_views
    assert bot_views[0].has_pending_edit is True
    assert bot_views[0].text.value == "active text"
    assert bot_views[1].status is RuleStatus.PROPOSED
    assert bot_views[1].effective_since is None
    assert bot_views[1].has_pending_edit is False
    assert bot_views[1].shared is True
