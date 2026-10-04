"""Shared selection of ACTIVE effective rules for LLM context."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from svoi_pravila.application.ports.unit_of_work import UnitOfWork
from svoi_pravila.domain.contact import Contact
from svoi_pravila.domain.enums import RuleCategory, RuleStatus
from svoi_pravila.domain.ids import RuleId
from svoi_pravila.domain.pair import Pair
from svoi_pravila.domain.rules import ContactScope, PairScope
from svoi_pravila.domain.text import RuleText


@dataclass(frozen=True, slots=True)
class EffectiveRuleView:
    """ACTIVE rule with its effective text for LLM context."""

    rule_id: RuleId
    category: RuleCategory
    text: RuleText
    effective_since: datetime


async def collect_effective_rules(
    uow: UnitOfWork,
    contact: Contact,
    pair: Pair | None,
) -> tuple[EffectiveRuleView, ...]:
    """Return ACTIVE rules with an effective revision, ordered for LLM context."""
    candidates = await uow.rules.list_for_scope(ContactScope(contact_id=contact.id))
    if pair is not None:
        candidates = [
            *candidates,
            *(await uow.rules.list_for_scope(PairScope(pair_id=pair.id))),
        ]
    views: list[EffectiveRuleView] = []
    for rule in candidates:
        if rule.status is not RuleStatus.ACTIVE:
            continue
        effective = rule.effective_revision
        if effective is None or effective.effective_since is None:
            continue
        views.append(
            EffectiveRuleView(
                rule_id=rule.id,
                category=rule.category,
                text=effective.text,
                effective_since=effective.effective_since,
            )
        )
    views.sort(key=lambda view: (view.effective_since, view.rule_id))
    return tuple(views)
