"""Store a one-time compose handoff and return button context (label never logged)."""

from __future__ import annotations

from dataclasses import dataclass

from svoi_pravila.application.compose_handoff import ComposeHandoffPayload
from svoi_pravila.application.errors import NotFound
from svoi_pravila.application.ports.compose_handoffs import ComposeHandoffs
from svoi_pravila.application.ports.consent_catalog import ConsentCatalog
from svoi_pravila.application.ports.pseudonymizer import Pseudonymizer
from svoi_pravila.application.ports.unit_of_work import UnitOfWorkFactory
from svoi_pravila.application.use_cases._access import require_access
from svoi_pravila.domain.ids import ContactId, TelegramUserId

_HANDOFF_PURPOSE = "compose_handoff"


@dataclass(frozen=True, slots=True)
class IssueComposeHandoffCommand:
    """Input for IssueComposeHandoff."""

    telegram_user_id: TelegramUserId
    draft: str


@dataclass(frozen=True, slots=True)
class IssueComposeHandoffResult:
    """Opaque handoff token and optional active-contact label for the results button."""

    token: str
    contact_label: str | None


@dataclass(frozen=True, slots=True)
class IssueComposeHandoffPorts:
    """Injected collaborators for IssueComposeHandoff."""

    uow_factory: UnitOfWorkFactory
    catalog: ConsentCatalog
    compose_handoffs: ComposeHandoffs
    pseudonymizer: Pseudonymizer


class IssueComposeHandoff:
    """Seal draft + active contact for a mini-app Compose deep link."""

    def __init__(self, ports: IssueComposeHandoffPorts) -> None:
        self._ports = ports

    async def execute(self, command: IssueComposeHandoffCommand) -> IssueComposeHandoffResult:
        """Store a single-use handoff; raise when the user is not onboarded."""
        async with self._ports.uow_factory() as uow:
            user = await uow.users.get_by_telegram_id(command.telegram_user_id)
            if user is None:
                raise NotFound()
            await require_access(uow, self._ports.catalog, user.id)
            contact_id: ContactId | None = user.active_contact_id
            contact_label: str | None = None
            if contact_id is not None:
                contact = await uow.contacts.get(contact_id)
                if contact is not None and contact.owner_id == user.id:
                    contact_label = contact.label.value
                else:
                    contact_id = None
        pseudonym = self._ports.pseudonymizer.pseudonymize(
            _HANDOFF_PURPOSE, str(command.telegram_user_id.value)
        )
        token = await self._ports.compose_handoffs.store(
            pseudonym,
            ComposeHandoffPayload(draft=command.draft, contact_id=contact_id),
        )
        return IssueComposeHandoffResult(token=token, contact_label=contact_label)
