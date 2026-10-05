"""Orchestrate ExportMyData and delivery to the bot chat."""

from __future__ import annotations

from dataclasses import dataclass

from svoi_pravila.application.errors import NotFound
from svoi_pravila.application.ports.export_delivery import ExportDelivery
from svoi_pravila.application.use_cases.export_my_data import ExportMyData, ExportMyDataCommand
from svoi_pravila.domain.ids import TelegramUserId


@dataclass(frozen=True, slots=True)
class RequestMyDataExportCommand:
    """Input for RequestMyDataExport."""

    telegram_user_id: TelegramUserId


@dataclass(frozen=True, slots=True)
class RequestMyDataExportResult:
    """Export was built and delivered to the bot chat."""

    delivered_to: str


class RequestMyDataExport:
    """Build the user's export and deliver it via ``ExportDelivery``."""

    def __init__(self, export_my_data: ExportMyData, delivery: ExportDelivery) -> None:
        self._export_my_data = export_my_data
        self._delivery = delivery

    async def execute(self, command: RequestMyDataExportCommand) -> RequestMyDataExportResult:
        """Export then deliver; never returns the payload to the caller."""
        result = await self._export_my_data.execute(
            ExportMyDataCommand(telegram_user_id=command.telegram_user_id)
        )
        if not result.found or result.payload is None:
            raise NotFound
        await self._delivery.deliver(command.telegram_user_id, result.payload)
        return RequestMyDataExportResult(delivered_to="bot_chat")
