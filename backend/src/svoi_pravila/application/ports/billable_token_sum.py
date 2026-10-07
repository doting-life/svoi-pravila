"""Read-only sum of billable tokens for a product day (budget rebuild)."""

from __future__ import annotations

from datetime import date
from typing import Protocol


class BillableTokenSum(Protocol):
    """Sum ``usage_events.billable_tokens`` for one product day."""

    async def sum_for_day(self, day: date) -> int:
        """Return the COALESCE(SUM(...), 0) for ``day`` in the analytics timezone."""
        ...
