"""Get effective ACTIVE rules for LLM context."""

from __future__ import annotations

from dataclasses import dataclass

from svoi_pravila.application.ports.consent_catalog import ConsentCatalog
from svoi_pravila.application.ports.unit_of_work import UnitOfWorkFactory
from svoi_pravila.application.use_cases._access import require_access
from svoi_pravila.application.use_cases._contact_access import load_owned_contact
from svoi_pravila.application.use_cases._effective_rules import (
    EffectiveRuleView,
    collect_effective_rules,
)
from svoi_pravila.domain.ids import ContactId, UserId

__all__ = [
    "EffectiveRuleView",
    "GetEffectiveRules",
    "GetEffectiveRulesCommand",
    "GetEffectiveRulesResult",
]


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
            views = await collect_effective_rules(uow, contact, pair)
            return GetEffectiveRulesResult(rules=views)
