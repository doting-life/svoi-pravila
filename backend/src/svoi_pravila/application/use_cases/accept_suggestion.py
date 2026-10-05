"""Accept a pending rule suggestion and propose a private rule."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from svoi_pravila.application.errors import NotFound, OpenRuleLimitReached
from svoi_pravila.application.ports.clock import Clock
from svoi_pravila.application.ports.consent_catalog import ConsentCatalog
from svoi_pravila.application.ports.id_generator import IdGenerator
from svoi_pravila.application.ports.unit_of_work import UnitOfWorkFactory
from svoi_pravila.application.use_cases._access import require_access
from svoi_pravila.application.use_cases._propose_rule import ProposeRuleParams, propose_rule_in_uow
from svoi_pravila.domain.enums import SuggestionStatus
from svoi_pravila.domain.ids import RuleSuggestionId, UserId
from svoi_pravila.domain.rule_suggestion import RuleSuggestion
from svoi_pravila.domain.rules import Rule


class AcceptSuggestionOutcome(StrEnum):
    """Typed outcome of AcceptSuggestion."""

    ACCEPTED = "accepted"
    ALREADY_DECIDED = "already_decided"
    OPEN_RULE_LIMIT = "open_rule_limit"


@dataclass(frozen=True, slots=True)
class AcceptSuggestionCommand:
    """Input for AcceptSuggestion."""

    actor_id: UserId
    suggestion_id: RuleSuggestionId


@dataclass(frozen=True, slots=True)
class AcceptSuggestionResult:
    """Result of AcceptSuggestion."""

    outcome: AcceptSuggestionOutcome
    suggestion: RuleSuggestion | None
    rule: Rule | None


class AcceptSuggestion:
    """Accept a pending suggestion and create a private ACTIVE rule in one UoW."""

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

    async def execute(self, command: AcceptSuggestionCommand) -> AcceptSuggestionResult:
        """Accept → ProposeRule(shared=False) in the same unit of work."""
        async with self._uow_factory() as uow:
            await require_access(uow, self._catalog, command.actor_id)
            suggestion = await uow.rule_suggestions.get(command.suggestion_id)
            if suggestion is None or suggestion.user_id != command.actor_id:
                raise NotFound()
            if suggestion.status is not SuggestionStatus.PENDING:
                return AcceptSuggestionResult(
                    outcome=AcceptSuggestionOutcome.ALREADY_DECIDED,
                    suggestion=suggestion,
                    rule=None,
                )
            accepted = suggestion.accept(self._clock.now())
            try:
                rule = await propose_rule_in_uow(
                    uow,
                    ids=self._ids,
                    clock=self._clock,
                    params=ProposeRuleParams(
                        actor_id=command.actor_id,
                        contact_id=suggestion.contact_id,
                        category=suggestion.category,
                        text=suggestion.text,
                        shared=False,
                    ),
                )
            except OpenRuleLimitReached:
                return AcceptSuggestionResult(
                    outcome=AcceptSuggestionOutcome.OPEN_RULE_LIMIT,
                    suggestion=suggestion,
                    rule=None,
                )
            await uow.rule_suggestions.update(accepted)
            await uow.commit()
            return AcceptSuggestionResult(
                outcome=AcceptSuggestionOutcome.ACCEPTED,
                suggestion=accepted,
                rule=rule,
            )
