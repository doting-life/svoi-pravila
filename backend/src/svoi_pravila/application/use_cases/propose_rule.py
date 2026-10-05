"""Propose a new rule (private or shared)."""

from __future__ import annotations

from dataclasses import dataclass

from svoi_pravila.application.ports.clock import Clock
from svoi_pravila.application.ports.consent_catalog import ConsentCatalog
from svoi_pravila.application.ports.id_generator import IdGenerator
from svoi_pravila.application.ports.unit_of_work import UnitOfWorkFactory
from svoi_pravila.application.use_cases._access import require_access
from svoi_pravila.application.use_cases._propose_rule import ProposeRuleParams, propose_rule_in_uow
from svoi_pravila.domain.enums import RuleCategory
from svoi_pravila.domain.ids import ContactId, UserId
from svoi_pravila.domain.rules import Rule
from svoi_pravila.domain.text import RuleText


@dataclass(frozen=True, slots=True)
class ProposeRuleCommand:
    """Input for ProposeRule."""

    actor_id: UserId
    contact_id: ContactId
    category: RuleCategory
    text: RuleText
    shared: bool


@dataclass(frozen=True, slots=True)
class ProposeRuleResult:
    """Result of ProposeRule."""

    rule: Rule


class ProposeRule:
    """Propose a private or shared rule for a contact's scope."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        catalog: ConsentCatalog,
        ids: IdGenerator,
        clock: Clock,
    ) -> None:
        self._uow_factory = uow_factory
        self._catalog = catalog
        self._ids = ids
        self._clock = clock

    async def execute(self, command: ProposeRuleCommand) -> ProposeRuleResult:
        """Create a rule with approvers derived from scope."""
        async with self._uow_factory() as uow:
            await require_access(uow, self._catalog, command.actor_id)
            rule = await propose_rule_in_uow(
                uow,
                ids=self._ids,
                clock=self._clock,
                params=ProposeRuleParams(
                    actor_id=command.actor_id,
                    contact_id=command.contact_id,
                    category=command.category,
                    text=command.text,
                    shared=command.shared,
                ),
            )
            await uow.commit()
            return ProposeRuleResult(rule=rule)
