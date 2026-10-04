"""Create a contact for the owner."""

from __future__ import annotations

from dataclasses import dataclass

from svoi_pravila.application.errors import ContactLimitReached, NotFound
from svoi_pravila.application.ports.clock import Clock
from svoi_pravila.application.ports.consent_catalog import ConsentCatalog
from svoi_pravila.application.ports.id_generator import IdGenerator
from svoi_pravila.application.ports.unit_of_work import UnitOfWorkFactory
from svoi_pravila.application.use_cases._access import require_access
from svoi_pravila.domain.contact import MAX_CONTACTS_PER_USER, Contact
from svoi_pravila.domain.enums import RelationshipKind
from svoi_pravila.domain.ids import ContactId, UserId
from svoi_pravila.domain.text import ContactLabel


@dataclass(frozen=True, slots=True)
class CreateContactCommand:
    """Input for CreateContact."""

    actor_id: UserId
    label: ContactLabel
    relationship: RelationshipKind


@dataclass(frozen=True, slots=True)
class CreateContactResult:
    """Result of CreateContact."""

    contact: Contact


class CreateContact:
    """Create a contact owned by the actor."""

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

    async def execute(self, command: CreateContactCommand) -> CreateContactResult:
        """Create a contact if under the per-user limit."""
        async with self._uow_factory() as uow:
            await require_access(uow, self._catalog, command.actor_id)
            count = await uow.contacts.count_for_owner(command.actor_id)
            if count >= MAX_CONTACTS_PER_USER:
                raise ContactLimitReached()
            contact = Contact(
                id=ContactId(self._ids.new_id()),
                owner_id=command.actor_id,
                label=command.label,
                relationship=command.relationship,
                pair_id=None,
                created_at=self._clock.now(),
            )
            await uow.contacts.add(contact)
            user = await uow.users.get(command.actor_id)
            if user is None:
                raise NotFound()
            if user.active_contact_id is None:
                await uow.users.update(user.set_active_contact(contact.id))
            await uow.commit()
            return CreateContactResult(contact=contact)
