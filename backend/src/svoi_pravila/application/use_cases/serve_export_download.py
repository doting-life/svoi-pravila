"""Consume a download grant and return the export payload once."""

from __future__ import annotations

from dataclasses import dataclass

from svoi_pravila.application.errors import NotFound
from svoi_pravila.application.ports.export_download import ExportDownloadStore
from svoi_pravila.application.ports.unit_of_work import UnitOfWorkFactory
from svoi_pravila.application.use_cases.export_my_data import ExportMyData, ExportMyDataCommand


@dataclass(frozen=True, slots=True)
class ServeExportDownloadCommand:
    """Input for ServeExportDownload."""

    raw_token: str


@dataclass(frozen=True, slots=True)
class ServeExportDownloadResult:
    """Export JSON payload for a single download response."""

    payload: dict[str, object]


class ServeExportDownload:
    """Atomically consume the grant, then build the same export as ExportMyData."""

    def __init__(
        self,
        store: ExportDownloadStore,
        export_my_data: ExportMyData,
        uow_factory: UnitOfWorkFactory,
    ) -> None:
        self._store = store
        self._export_my_data = export_my_data
        self._uow_factory = uow_factory

    async def execute(self, command: ServeExportDownloadCommand) -> ServeExportDownloadResult:
        """Consume token first so reuse is impossible even if export fails later."""
        user_id = await self._store.consume(command.raw_token)
        if user_id is None:
            raise NotFound
        async with self._uow_factory() as uow:
            user = await uow.users.get(user_id)
            if user is None:
                raise NotFound
            telegram_user_id = user.telegram_user_id
        result = await self._export_my_data.execute(
            ExportMyDataCommand(telegram_user_id=telegram_user_id)
        )
        if not result.found or result.payload is None:
            raise NotFound
        return ServeExportDownloadResult(payload=result.payload)
