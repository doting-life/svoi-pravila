"""List pending rule suggestions for a contact."""

from __future__ import annotations

from dataclasses import dataclass

from svoi_pravila.application.errors import NotFound
from svoi_pravila.application.ports.consent_catalog import ConsentCatalog
from svoi_pravila.application.ports.unit_of_work import UnitOfWorkFactory
from svoi_pravila.application.use_cases._access import require_access
from svoi_pravila.domain.ids import ContactId, UserId
from svoi_pravila.domain.rule_suggestion import RuleSuggestion


@dataclass(frozen=True, slots=True)
class ListSuggestionsCommand:
    """Input for ListSuggestions."""

    actor_id: UserId
    contact_id: ContactId


@dataclass(frozen=True, slots=True)
class ListSuggestionsResult:
    """Pending suggestions for the contact."""

    suggestions: tuple[RuleSuggestion, ...]


class ListSuggestions:
    """Return pending rule suggestions owned by the actor for a contact."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        catalog: ConsentCatalog,
    ) -> None:
        self._uow_factory = uow_factory
        self._catalog = catalog

    async def execute(self, command: ListSuggestionsCommand) -> ListSuggestionsResult:
        """List pending suggestions after access and ownership checks."""
        async with self._uow_factory() as uow:
            await require_access(uow, self._catalog, command.actor_id)
            contact = await uow.contacts.get(command.contact_id)
            if contact is None or contact.owner_id != command.actor_id:
                raise NotFound()
            pending = await uow.rule_suggestions.list_pending_for_contact(
                command.actor_id,
                command.contact_id,
            )
            return ListSuggestionsResult(suggestions=tuple(pending))
