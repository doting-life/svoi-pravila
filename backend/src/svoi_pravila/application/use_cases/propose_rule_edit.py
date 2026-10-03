"""Propose an edit to an active rule."""

from __future__ import annotations

from dataclasses import dataclass

from svoi_pravila.application.ports.clock import Clock
from svoi_pravila.application.ports.consent_catalog import ConsentCatalog
from svoi_pravila.application.ports.unit_of_work import UnitOfWorkFactory
from svoi_pravila.application.use_cases._access import require_access
from svoi_pravila.application.use_cases._contact_access import load_rule_for_approver
from svoi_pravila.domain.ids import RuleId, UserId
from svoi_pravila.domain.rules import Rule
from svoi_pravila.domain.text import RuleText


@dataclass(frozen=True, slots=True)
class ProposeRuleEditCommand:
    """Input for ProposeRuleEdit."""

    actor_id: UserId
    rule_id: RuleId
    text: RuleText


@dataclass(frozen=True, slots=True)
class ProposeRuleEditResult:
    """Result of ProposeRuleEdit."""

    rule: Rule


class ProposeRuleEdit:
    """Propose a new revision for an active rule."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        catalog: ConsentCatalog,
        clock: Clock,
    ) -> None:
        self._uow_factory = uow_factory
        self._catalog = catalog
        self._clock = clock

    async def execute(self, command: ProposeRuleEditCommand) -> ProposeRuleEditResult:
        """Propose an edit or raise NotFound / domain transition errors."""
        async with self._uow_factory() as uow:
            await require_access(uow, self._catalog, command.actor_id)
            rule = await load_rule_for_approver(uow, command.actor_id, command.rule_id)
            updated = rule.propose_edit(command.actor_id, command.text, self._clock.now())
            await uow.rules.update(updated)
            await uow.commit()
            return ProposeRuleEditResult(rule=updated)
