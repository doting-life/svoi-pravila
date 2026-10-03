"""Propose a new rule (private or shared)."""

from __future__ import annotations

from dataclasses import dataclass

from svoi_pravila.application.errors import NotFound, OpenRuleLimitReached
from svoi_pravila.application.ports.clock import Clock
from svoi_pravila.application.ports.consent_catalog import ConsentCatalog
from svoi_pravila.application.ports.id_generator import IdGenerator
from svoi_pravila.application.ports.unit_of_work import UnitOfWorkFactory
from svoi_pravila.application.use_cases._access import require_access
from svoi_pravila.domain.enums import RuleCategory
from svoi_pravila.domain.ids import ContactId, RuleId, UserId
from svoi_pravila.domain.rules import (
    MAX_OPEN_RULES_PER_SCOPE,
    ContactScope,
    PairScope,
    Rule,
)
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
            contact = await uow.contacts.get(command.contact_id)
            if contact is None:
                raise NotFound()

            if command.shared:
                if contact.pair_id is None:
                    raise NotFound()
                pair = await uow.pairs.get(contact.pair_id)
                if pair is None or not pair.is_member(command.actor_id):
                    raise NotFound()
                scope: ContactScope | PairScope = PairScope(pair_id=pair.id)
                approvers = frozenset(pair.members)
            else:
                if contact.owner_id != command.actor_id:
                    raise NotFound()
                scope = ContactScope(contact_id=contact.id)
                approvers = frozenset({command.actor_id})

            open_count = await uow.rules.count_open_for_scope(scope)
            if open_count >= MAX_OPEN_RULES_PER_SCOPE:
                raise OpenRuleLimitReached()

            rule = Rule.propose(
                rule_id=RuleId(self._ids.new_id()),
                scope=scope,
                category=command.category,
                approvers=approvers,
                author_id=command.actor_id,
                text=command.text,
                now=self._clock.now(),
            )
            await uow.rules.add(rule)
            await uow.commit()
            return ProposeRuleResult(rule=rule)
