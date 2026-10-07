"""Issue a one-time export download grant for the authenticated user."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from svoi_pravila.application.errors import NotFound
from svoi_pravila.application.ports.clock import Clock
from svoi_pravila.application.ports.export_download import (
    EXPORT_DOWNLOAD_TTL_SECONDS,
    ExportDownloadGrant,
    ExportDownloadStore,
)
from svoi_pravila.application.ports.unit_of_work import UnitOfWorkFactory
from svoi_pravila.domain.ids import TelegramUserId


@dataclass(frozen=True, slots=True)
class IssueExportDownloadCommand:
    """Input for IssueExportDownload."""

    telegram_user_id: TelegramUserId


@dataclass(frozen=True, slots=True)
class IssueExportDownloadResult:
    """One-time download capability (raw token returned once)."""

    grant: ExportDownloadGrant


class IssueExportDownload:
    """Create a short-lived download grant bound to the user's id."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        store: ExportDownloadStore,
        clock: Clock,
    ) -> None:
        self._uow_factory = uow_factory
        self._store = store
        self._clock = clock

    async def execute(self, command: IssueExportDownloadCommand) -> IssueExportDownloadResult:
        """Require an existing user; issue a hashed Valkey grant."""
        async with self._uow_factory() as uow:
            user = await uow.users.get_by_telegram_id(command.telegram_user_id)
            if user is None:
                raise NotFound
            user_id = user.id
        expires_at = self._clock.now() + timedelta(seconds=EXPORT_DOWNLOAD_TTL_SECONDS)
        grant = await self._store.issue(user_id, expires_at=expires_at)
        return IssueExportDownloadResult(grant=grant)
