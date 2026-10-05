"""Shared projection of rules for management lists (bot and mini-app)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from svoi_pravila.domain.enums import RuleCategory, RuleStatus
from svoi_pravila.domain.ids import RuleId
from svoi_pravila.domain.rules import PairScope, Rule
from svoi_pravila.domain.text import RuleText


@dataclass(frozen=True, slots=True)
class RuleListItemView:
    """One ACTIVE or PROPOSED rule as shown in management lists."""

    rule_id: RuleId
    category: RuleCategory
    status: RuleStatus
    text: RuleText
    shared: bool
    created_at: datetime
    effective_since: datetime | None
    has_pending_edit: bool


def project_rules_for_list(rules: Sequence[Rule]) -> tuple[RuleListItemView, ...]:
    """Project rules for list UIs: ACTIVE/PROPOSED only; effective text for ACTIVE."""
    views: list[RuleListItemView] = []
    for rule in rules:
        view = _project_one(rule)
        if view is not None:
            views.append(view)
    return tuple(views)


def _project_one(rule: Rule) -> RuleListItemView | None:
    shared = isinstance(rule.scope, PairScope)
    if rule.status is RuleStatus.ACTIVE:
        revision = rule.require_effective_revision()
        return RuleListItemView(
            rule_id=rule.id,
            category=rule.category,
            status=rule.status,
            text=revision.text,
            shared=shared,
            created_at=rule.created_at,
            effective_since=revision.require_effective_since(),
            has_pending_edit=rule.pending_revision is not None,
        )
    if rule.status is RuleStatus.PROPOSED:
        revision = rule.pending_revision or rule.revisions[-1]
        return RuleListItemView(
            rule_id=rule.id,
            category=rule.category,
            status=rule.status,
            text=revision.text,
            shared=shared,
            created_at=rule.created_at,
            effective_since=None,
            has_pending_edit=False,
        )
    return None
