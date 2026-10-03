"""Get access status for a user."""

from __future__ import annotations

from dataclasses import dataclass

from svoi_pravila.application.ports.consent_catalog import ConsentCatalog
from svoi_pravila.application.ports.unit_of_work import UnitOfWorkFactory
from svoi_pravila.application.use_cases._access import load_access_status
from svoi_pravila.domain.access import AccessStatus
from svoi_pravila.domain.ids import UserId


@dataclass(frozen=True, slots=True)
class GetAccessStatusCommand:
    """Input for GetAccessStatus."""

    user_id: UserId


@dataclass(frozen=True, slots=True)
class GetAccessStatusResult:
    """Result of GetAccessStatus."""

    status: AccessStatus


class GetAccessStatus:
    """Evaluate age confirmation and consent completeness."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        catalog: ConsentCatalog,
    ) -> None:
        self._uow_factory = uow_factory
        self._catalog = catalog

    async def execute(self, command: GetAccessStatusCommand) -> GetAccessStatusResult:
        """Return the current access status."""
        async with self._uow_factory() as uow:
            status = await load_access_status(uow, self._catalog, command.user_id)
            return GetAccessStatusResult(status=status)
