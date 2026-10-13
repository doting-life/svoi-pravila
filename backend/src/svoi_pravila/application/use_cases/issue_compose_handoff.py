"""Store a one-time compose handoff that seals the inline draft only."""

from __future__ import annotations

from dataclasses import dataclass

from svoi_pravila.application.compose_handoff import ComposeHandoffPayload
from svoi_pravila.application.errors import NotFound
from svoi_pravila.application.ports.compose_handoffs import ComposeHandoffs
from svoi_pravila.application.ports.consent_catalog import ConsentCatalog
from svoi_pravila.application.ports.unit_of_work import UnitOfWorkFactory
from svoi_pravila.application.use_cases._access import require_access
from svoi_pravila.domain.ids import TelegramUserId


@dataclass(frozen=True, slots=True)
class IssueComposeHandoffCommand:
    """Input for IssueComposeHandoff."""

    telegram_user_id: TelegramUserId
    draft: str


@dataclass(frozen=True, slots=True)
class IssueComposeHandoffResult:
    """Opaque handoff token for the results button."""

    token: str


@dataclass(frozen=True, slots=True)
class IssueComposeHandoffPorts:
    """Injected collaborators for IssueComposeHandoff."""

    uow_factory: UnitOfWorkFactory
    catalog: ConsentCatalog
    compose_handoffs: ComposeHandoffs


class IssueComposeHandoff:
    """Seal the draft for a mini-app Compose deep link (never a contact)."""

    def __init__(self, ports: IssueComposeHandoffPorts) -> None:
        self._ports = ports

    async def execute(self, command: IssueComposeHandoffCommand) -> IssueComposeHandoffResult:
        """Store a single-use bearer handoff; raise when the user is not onboarded."""
        async with self._ports.uow_factory() as uow:
            user = await uow.users.get_by_telegram_id(command.telegram_user_id)
            if user is None:
                raise NotFound()
            await require_access(uow, self._ports.catalog, user.id)
            user_id = user.id
        token = await self._ports.compose_handoffs.store(
            ComposeHandoffPayload(
                user_id=user_id,
                draft=command.draft,
            ),
        )
        return IssueComposeHandoffResult(token=token)
