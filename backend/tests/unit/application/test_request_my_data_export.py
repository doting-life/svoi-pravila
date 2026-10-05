"""Unit tests for RequestMyDataExport orchestrator."""

from __future__ import annotations

import pytest

from svoi_pravila.application.errors import BotChatUnavailable, NotFound
from svoi_pravila.application.use_cases.export_my_data import ExportMyData
from svoi_pravila.application.use_cases.request_my_data_export import (
    RequestMyDataExport,
    RequestMyDataExportCommand,
)
from svoi_pravila.domain.ids import TelegramUserId
from tests.fakes.export_delivery import FakeExportDelivery
from tests.unit.application.conftest import AppWorld


@pytest.mark.unit
async def test_request_export_delivers_payload(world: AppWorld) -> None:
    await world.ensure_granted_user(501)
    delivery = FakeExportDelivery()
    uc = RequestMyDataExport(ExportMyData(world.uow_factory, world.clock), delivery)
    result = await uc.execute(RequestMyDataExportCommand(TelegramUserId(501)))
    assert result.delivered_to == "bot_chat"
    assert len(delivery.deliveries) == 1
    assert delivery.deliveries[0][0] == TelegramUserId(501)
    assert delivery.deliveries[0][1]["export_version"] == 1


@pytest.mark.unit
async def test_request_export_unknown_user(world: AppWorld) -> None:
    delivery = FakeExportDelivery()
    uc = RequestMyDataExport(ExportMyData(world.uow_factory, world.clock), delivery)
    with pytest.raises(NotFound):
        await uc.execute(RequestMyDataExportCommand(TelegramUserId(999)))
    assert delivery.deliveries == []


@pytest.mark.unit
async def test_request_export_propagates_bot_chat_unavailable(world: AppWorld) -> None:
    await world.ensure_granted_user(502)
    delivery = FakeExportDelivery(unavailable=True)
    uc = RequestMyDataExport(ExportMyData(world.uow_factory, world.clock), delivery)
    with pytest.raises(BotChatUnavailable):
        await uc.execute(RequestMyDataExportCommand(TelegramUserId(502)))
