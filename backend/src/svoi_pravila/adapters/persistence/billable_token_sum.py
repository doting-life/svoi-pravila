"""Sum billable tokens for a product day via the usage-event repository."""

from __future__ import annotations

from datetime import date

from svoi_pravila.application.ports.unit_of_work import UnitOfWorkFactory


class UowBillableTokenSum:
    """Opens a short read UoW to SUM ``usage_events.billable_tokens``."""

    def __init__(self, uow_factory: UnitOfWorkFactory, *, timezone: str) -> None:
        self._uow_factory = uow_factory
        self._timezone = timezone

    async def sum_for_day(self, day: date) -> int:
        """Return COALESCE(SUM(billable_tokens), 0) for the product day."""
        async with self._uow_factory() as uow:
            return await uow.usage_events.sum_billable_for_day(day, self._timezone)
