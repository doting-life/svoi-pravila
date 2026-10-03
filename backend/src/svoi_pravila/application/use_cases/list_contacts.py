"""List contacts for the owner."""

from __future__ import annotations

from dataclasses import dataclass

from svoi_pravila.application.ports.consent_catalog import ConsentCatalog
from svoi_pravila.application.ports.unit_of_work import UnitOfWorkFactory
from svoi_pravila.application.use_cases._access import require_access
from svoi_pravila.domain.contact import Contact
from svoi_pravila.domain.ids import UserId


@dataclass(frozen=True, slots=True)
class ListContactsCommand:
    """Input for ListContacts."""

    actor_id: UserId


@dataclass(frozen=True, slots=True)
class ListContactsResult:
    """Result of ListContacts."""

    contacts: tuple[Contact, ...]


class ListContacts:
    """List contacts owned by the actor."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        catalog: ConsentCatalog,
    ) -> None:
        self._uow_factory = uow_factory
        self._catalog = catalog

    async def execute(self, command: ListContactsCommand) -> ListContactsResult:
        """Return the actor's contacts."""
        async with self._uow_factory() as uow:
            await require_access(uow, self._catalog, command.actor_id)
            contacts = await uow.contacts.list_for_owner(command.actor_id)
            return ListContactsResult(contacts=tuple(contacts))
