"""Set the actor's active contact."""

from __future__ import annotations

from dataclasses import dataclass

from svoi_pravila.application.errors import NotFound
from svoi_pravila.application.ports.consent_catalog import ConsentCatalog
from svoi_pravila.application.ports.unit_of_work import UnitOfWorkFactory
from svoi_pravila.application.use_cases._access import require_access
from svoi_pravila.domain.ids import ContactId, UserId
from svoi_pravila.domain.user import User


@dataclass(frozen=True, slots=True)
class SetActiveContactCommand:
    """Input for SetActiveContact."""

    actor_id: UserId
    contact_id: ContactId


@dataclass(frozen=True, slots=True)
class SetActiveContactResult:
    """Result of SetActiveContact."""

    user: User


class SetActiveContact:
    """Set active contact to one owned by the actor."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        catalog: ConsentCatalog,
    ) -> None:
        self._uow_factory = uow_factory
        self._catalog = catalog

    async def execute(self, command: SetActiveContactCommand) -> SetActiveContactResult:
        """Set the active contact or raise NotFound."""
        async with self._uow_factory() as uow:
            await require_access(uow, self._catalog, command.actor_id)
            contact = await uow.contacts.get(command.contact_id)
            if contact is None or contact.owner_id != command.actor_id:
                raise NotFound()
            user = await uow.users.get(command.actor_id)
            if user is None:
                raise NotFound()
            updated = user.set_active_contact(command.contact_id)
            await uow.users.update(updated)
            await uow.commit()
            return SetActiveContactResult(user=updated)
