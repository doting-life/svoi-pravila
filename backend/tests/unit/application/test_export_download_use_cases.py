"""Unit tests for IssueExportDownload and ServeExportDownload."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from svoi_pravila.application.errors import NotFound
from svoi_pravila.application.use_cases.accept_age_confirmation import (
    AcceptAgeConfirmation,
    AcceptAgeConfirmationCommand,
)
from svoi_pravila.application.use_cases.export_my_data import ExportMyData
from svoi_pravila.application.use_cases.issue_export_download import (
    IssueExportDownload,
    IssueExportDownloadCommand,
)
from svoi_pravila.application.use_cases.serve_export_download import (
    ServeExportDownload,
    ServeExportDownloadCommand,
)
from svoi_pravila.domain.ids import TelegramUserId, UserId
from tests.fakes.clock import FakeClock
from tests.fakes.export_download import FakeExportDownloadStore
from tests.fakes.ids import FakeIdGenerator
from tests.fakes.uow import InMemoryUnitOfWorkFactory

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.mark.unit
async def test_issue_export_download_unknown_user() -> None:
    store = FakeExportDownloadStore()
    uc = IssueExportDownload(InMemoryUnitOfWorkFactory(), store, FakeClock(_NOW))
    with pytest.raises(NotFound):
        await uc.execute(IssueExportDownloadCommand(TelegramUserId(999)))


@pytest.mark.unit
async def test_serve_export_download_missing_user_after_consume() -> None:
    uow = InMemoryUnitOfWorkFactory()
    store = FakeExportDownloadStore()
    ghost = UserId(UUID(int=42))
    grant = await store.issue(ghost, expires_at=_NOW + timedelta(seconds=120))
    uc = ServeExportDownload(store, ExportMyData(uow, FakeClock(_NOW)), uow)
    with pytest.raises(NotFound):
        await uc.execute(ServeExportDownloadCommand(grant.raw_token))


@pytest.mark.unit
async def test_serve_export_download_export_not_found() -> None:
    """User row exists for consume lookup; ExportMyData uses an empty store."""
    uow_with_user = InMemoryUnitOfWorkFactory()
    clock = FakeClock(_NOW)
    ids = FakeIdGenerator()
    accepted = await AcceptAgeConfirmation(uow_with_user, ids, clock).execute(
        AcceptAgeConfirmationCommand(TelegramUserId(7))
    )
    store = FakeExportDownloadStore()
    grant = await store.issue(accepted.user.id, expires_at=_NOW + timedelta(seconds=120))
    empty_uow = InMemoryUnitOfWorkFactory()
    uc = ServeExportDownload(store, ExportMyData(empty_uow, clock), uow_with_user)
    with pytest.raises(NotFound):
        await uc.execute(ServeExportDownloadCommand(grant.raw_token))


@pytest.mark.unit
async def test_issue_and_serve_round_trip() -> None:
    uow = InMemoryUnitOfWorkFactory()
    clock = FakeClock(_NOW)
    ids = FakeIdGenerator()
    await AcceptAgeConfirmation(uow, ids, clock).execute(
        AcceptAgeConfirmationCommand(TelegramUserId(11))
    )
    store = FakeExportDownloadStore()
    issued = await IssueExportDownload(uow, store, clock).execute(
        IssueExportDownloadCommand(TelegramUserId(11))
    )
    served = await ServeExportDownload(store, ExportMyData(uow, clock), uow).execute(
        ServeExportDownloadCommand(issued.grant.raw_token)
    )
    assert served.payload["export_version"] == 1
    with pytest.raises(NotFound):
        await ServeExportDownload(store, ExportMyData(uow, clock), uow).execute(
            ServeExportDownloadCommand(issued.grant.raw_token)
        )
