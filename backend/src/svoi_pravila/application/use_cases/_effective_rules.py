"""Shared selection of ACTIVE effective rules for LLM context."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from svoi_pravila.application.ports.unit_of_work import UnitOfWork
from svoi_pravila.domain.contact import Contact
from svoi_pravila.domain.enums import RuleCategory, RuleStatus
from svoi_pravila.domain.ids import RuleId
from svoi_pravila.domain.pair import Pair
from svoi_pravila.domain.rules import ContactScope, PairScope, RuleRevision
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


@dataclass(frozen=True, slots=True)
class VisibleRuleView:
    """A rule visible to the owner of ``contact``, any status, all revisions."""

    rule_id: RuleId
    category: RuleCategory
    status: RuleStatus
    created_at: datetime
    scope_kind: str
    revisions: tuple[RuleRevision, ...]


async def collect_visible_rules(
    uow: UnitOfWork,
    contact: Contact,
    pair: Pair | None,
) -> tuple[VisibleRuleView, ...]:
    """Return owned contact-scope rules plus pair-scope rules when linked."""
    candidates = [
        (rule, "contact")
        for rule in await uow.rules.list_for_scope(ContactScope(contact_id=contact.id))
    ]
    if pair is not None:
        candidates.extend(
            (rule, "pair") for rule in await uow.rules.list_for_scope(PairScope(pair_id=pair.id))
        )
    views = [
        VisibleRuleView(
            rule_id=rule.id,
            category=rule.category,
            status=rule.status,
            created_at=rule.created_at,
            scope_kind=scope_kind,
            revisions=rule.revisions,
        )
        for rule, scope_kind in candidates
    ]
    views.sort(key=lambda view: (view.created_at, view.rule_id))
    return tuple(views)
