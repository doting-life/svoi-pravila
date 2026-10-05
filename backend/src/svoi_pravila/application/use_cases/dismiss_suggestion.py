"""Dismiss a pending rule suggestion."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from svoi_pravila.application.errors import NotFound
from svoi_pravila.application.ports.clock import Clock
from svoi_pravila.application.ports.consent_catalog import ConsentCatalog
from svoi_pravila.application.ports.unit_of_work import UnitOfWorkFactory
from svoi_pravila.application.use_cases._access import require_access
from svoi_pravila.domain.enums import SuggestionStatus
from svoi_pravila.domain.ids import RuleSuggestionId, UserId
from svoi_pravila.domain.rule_suggestion import RuleSuggestion


class DismissSuggestionOutcome(StrEnum):
    """Typed outcome of DismissSuggestion."""

    DISMISSED = "dismissed"
    ALREADY_DECIDED = "already_decided"


@dataclass(frozen=True, slots=True)
class DismissSuggestionCommand:
    """Input for DismissSuggestion."""

    actor_id: UserId
    suggestion_id: RuleSuggestionId


@dataclass(frozen=True, slots=True)
class DismissSuggestionResult:
    """Result of DismissSuggestion."""

    outcome: DismissSuggestionOutcome
    suggestion: RuleSuggestion | None


class DismissSuggestion:
    """Dismiss a pending suggestion owned by the actor."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        catalog: ConsentCatalog,
        clock: Clock,
    ) -> None:
        self._uow_factory = uow_factory
        self._catalog = catalog
        self._clock = clock

    async def execute(self, command: DismissSuggestionCommand) -> DismissSuggestionResult:
        """Mark the suggestion dismissed after access and ownership checks."""
        async with self._uow_factory() as uow:
            await require_access(uow, self._catalog, command.actor_id)
            suggestion = await uow.rule_suggestions.get(command.suggestion_id)
            if suggestion is None or suggestion.user_id != command.actor_id:
                raise NotFound()
            if suggestion.status is not SuggestionStatus.PENDING:
                return DismissSuggestionResult(
                    outcome=DismissSuggestionOutcome.ALREADY_DECIDED,
                    suggestion=suggestion,
                )
            dismissed = suggestion.dismiss(self._clock.now())
            await uow.rule_suggestions.update(dismissed)
            await uow.commit()
            return DismissSuggestionResult(
                outcome=DismissSuggestionOutcome.DISMISSED,
                suggestion=dismissed,
            )
