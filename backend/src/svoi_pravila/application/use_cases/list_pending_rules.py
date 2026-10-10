"""List rules awaiting the actor's decision across all contacts."""

from __future__ import annotations

from dataclasses import dataclass

from svoi_pravila.application.ports.consent_catalog import ConsentCatalog
from svoi_pravila.application.ports.unit_of_work import UnitOfWorkFactory
from svoi_pravila.application.use_cases._access import require_access
from svoi_pravila.domain.enums import RuleCategory, RuleStatus
from svoi_pravila.domain.ids import ContactId, RuleId, UserId
from svoi_pravila.domain.rules import PairScope, Rule
from svoi_pravila.domain.text import ContactLabel, RuleText


@dataclass(frozen=True, slots=True)
class ListPendingRulesQuery:
    """Input for ListPendingRules."""

    actor_id: UserId


@dataclass(frozen=True, slots=True)
class PendingRuleItem:
    """One rule awaiting the actor's approve/reject decision."""

    rule_id: RuleId
    contact_id: ContactId
    contact_label: ContactLabel
    category: RuleCategory
    status: RuleStatus
    text: RuleText
    shared: bool
    has_pending_edit: bool


@dataclass(frozen=True, slots=True)
class ListPendingRulesResult:
    """Result of ListPendingRules."""

    items: tuple[PendingRuleItem, ...]


def needs_actor_decision(rule: Rule, actor_id: UserId) -> bool:
    """True when the actor must approve or reject a pending revision."""
    if not isinstance(rule.scope, PairScope):
        return False
    pending = rule.pending_revision
    if pending is None:
        return False
    if actor_id not in rule.approvers:
        return False
    if actor_id == pending.author_id:
        return False
    if rule.status is RuleStatus.PROPOSED:
        return True
    return rule.status is RuleStatus.ACTIVE


class ListPendingRules:
    """List shared rules and edits awaiting the current user's decision."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        catalog: ConsentCatalog,
    ) -> None:
        self._uow_factory = uow_factory
        self._catalog = catalog

    async def execute(self, query: ListPendingRulesQuery) -> ListPendingRulesResult:
        """Return pending items for every contact owned by the actor."""
        async with self._uow_factory() as uow:
            await require_access(uow, self._catalog, query.actor_id)
            contacts = await uow.contacts.list_for_owner(query.actor_id)
            items: list[PendingRuleItem] = []
            seen_rules: set[RuleId] = set()
            for contact in contacts:
                if contact.pair_id is None:
                    continue
                rules = await uow.rules.list_for_scope(PairScope(pair_id=contact.pair_id))
                for rule in rules:
                    if rule.id in seen_rules:
                        continue
                    if not needs_actor_decision(rule, query.actor_id):
                        continue
                    pending = rule.pending_revision
                    if pending is None:
                        continue
                    seen_rules.add(rule.id)
                    items.append(
                        PendingRuleItem(
                            rule_id=rule.id,
                            contact_id=contact.id,
                            contact_label=contact.label,
                            category=rule.category,
                            status=rule.status,
                            text=pending.text,
                            shared=True,
                            has_pending_edit=rule.status is RuleStatus.ACTIVE,
                        )
                    )
            items.sort(key=lambda item: str(item.rule_id))
            return ListPendingRulesResult(items=tuple(items))
