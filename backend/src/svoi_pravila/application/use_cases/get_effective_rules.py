"""Get effective ACTIVE rules for LLM context."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from svoi_pravila.application.ports.consent_catalog import ConsentCatalog
from svoi_pravila.application.ports.unit_of_work import UnitOfWorkFactory
from svoi_pravila.application.use_cases._access import require_access
from svoi_pravila.application.use_cases._contact_access import load_owned_contact
from svoi_pravila.domain.enums import RuleCategory, RuleStatus
from svoi_pravila.domain.ids import ContactId, RuleId, UserId
from svoi_pravila.domain.rules import ContactScope, PairScope
from svoi_pravila.domain.text import RuleText


@dataclass(frozen=True, slots=True)
class EffectiveRuleView:
    """ACTIVE rule with its effective text for LLM context."""

    rule_id: RuleId
    category: RuleCategory
    text: RuleText
    effective_since: datetime


@dataclass(frozen=True, slots=True)
class GetEffectiveRulesCommand:
    """Input for GetEffectiveRules."""

    actor_id: UserId
    contact_id: ContactId


@dataclass(frozen=True, slots=True)
class GetEffectiveRulesResult:
    """Result of GetEffectiveRules."""

    rules: tuple[EffectiveRuleView, ...]


class GetEffectiveRules:
    """Return ACTIVE rules with effective text for a contact's scopes."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        catalog: ConsentCatalog,
    ) -> None:
        self._uow_factory = uow_factory
        self._catalog = catalog

    async def execute(self, command: GetEffectiveRulesCommand) -> GetEffectiveRulesResult:
        """Return effective rules for a contact owned by the actor."""
        async with self._uow_factory() as uow:
            await require_access(uow, self._catalog, command.actor_id)
            contact = await uow.contacts.get(command.contact_id)
            contact, pair = await load_owned_contact(uow, command.actor_id, contact)
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
            views.sort(key=lambda v: (v.effective_since, v.rule_id))
            return GetEffectiveRulesResult(rules=tuple(views))
