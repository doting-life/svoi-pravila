"""Rename a contact."""

from __future__ import annotations

from dataclasses import dataclass

from svoi_pravila.application.errors import NotFound
from svoi_pravila.application.ports.consent_catalog import ConsentCatalog
from svoi_pravila.application.ports.unit_of_work import UnitOfWorkFactory
from svoi_pravila.application.use_cases._access import require_access
from svoi_pravila.domain.contact import Contact
from svoi_pravila.domain.ids import ContactId, UserId
from svoi_pravila.domain.text import ContactLabel


@dataclass(frozen=True, slots=True)
class RenameContactCommand:
    """Input for RenameContact."""

    actor_id: UserId
    contact_id: ContactId
    label: ContactLabel


@dataclass(frozen=True, slots=True)
class RenameContactResult:
    """Result of RenameContact."""

    contact: Contact


class RenameContact:
    """Rename a contact owned by the actor."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        catalog: ConsentCatalog,
    ) -> None:
        self._uow_factory = uow_factory
        self._catalog = catalog

    async def execute(self, command: RenameContactCommand) -> RenameContactResult:
        """Rename the contact or raise NotFound."""
        async with self._uow_factory() as uow:
            await require_access(uow, self._catalog, command.actor_id)
            contact = await uow.contacts.get(command.contact_id)
            if contact is None or contact.owner_id != command.actor_id:
                raise NotFound()
            updated = contact.rename(command.label)
            await uow.contacts.update(updated)
            await uow.commit()
            return RenameContactResult(contact=updated)
