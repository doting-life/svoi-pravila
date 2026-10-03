"""List rules for management UI."""

from __future__ import annotations

from dataclasses import dataclass

from svoi_pravila.application.ports.consent_catalog import ConsentCatalog
from svoi_pravila.application.ports.unit_of_work import UnitOfWorkFactory
from svoi_pravila.application.use_cases._access import require_access
from svoi_pravila.application.use_cases._contact_access import load_owned_contact
from svoi_pravila.domain.ids import ContactId, UserId
from svoi_pravila.domain.rules import ContactScope, PairScope, Rule


@dataclass(frozen=True, slots=True)
class ListRulesCommand:
    """Input for ListRules."""

    actor_id: UserId
    contact_id: ContactId


@dataclass(frozen=True, slots=True)
class ListRulesResult:
    """Result of ListRules."""

    rules: tuple[Rule, ...]


class ListRules:
    """List private contact rules plus shared pair rules (all statuses)."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        catalog: ConsentCatalog,
    ) -> None:
        self._uow_factory = uow_factory
        self._catalog = catalog

    async def execute(self, command: ListRulesCommand) -> ListRulesResult:
        """Return rules for a contact owned by the actor."""
        async with self._uow_factory() as uow:
            await require_access(uow, self._catalog, command.actor_id)
            contact = await uow.contacts.get(command.contact_id)
            contact, pair = await load_owned_contact(uow, command.actor_id, contact)
            private = await uow.rules.list_for_scope(ContactScope(contact_id=contact.id))
            shared: list[Rule] = []
            if pair is not None:
                shared = await uow.rules.list_for_scope(PairScope(pair_id=pair.id))
            return ListRulesResult(rules=(*private, *shared))
