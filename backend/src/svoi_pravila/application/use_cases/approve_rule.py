"""Approve a pending rule revision."""

from __future__ import annotations

from dataclasses import dataclass

from svoi_pravila.application.ports.clock import Clock
from svoi_pravila.application.ports.consent_catalog import ConsentCatalog
from svoi_pravila.application.ports.unit_of_work import UnitOfWorkFactory
from svoi_pravila.application.use_cases._access import require_access
from svoi_pravila.application.use_cases._contact_access import load_rule_for_approver
from svoi_pravila.domain.ids import RuleId, UserId
from svoi_pravila.domain.rules import Rule


@dataclass(frozen=True, slots=True)
class ApproveRuleCommand:
    """Input for ApproveRule."""

    actor_id: UserId
    rule_id: RuleId


@dataclass(frozen=True, slots=True)
class ApproveRuleResult:
    """Result of ApproveRule."""

    rule: Rule


class ApproveRule:
    """Approve the pending revision of a rule."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        catalog: ConsentCatalog,
        clock: Clock,
    ) -> None:
        self._uow_factory = uow_factory
        self._catalog = catalog
        self._clock = clock

    async def execute(self, command: ApproveRuleCommand) -> ApproveRuleResult:
        """Approve or raise NotFound / domain transition errors."""
        async with self._uow_factory() as uow:
            await require_access(uow, self._catalog, command.actor_id)
            rule = await load_rule_for_approver(uow, command.actor_id, command.rule_id)
            updated = rule.approve(command.actor_id, self._clock.now())
            await uow.rules.update(updated)
            await uow.commit()
            return ApproveRuleResult(rule=updated)
