"""Record one LLM price and report which days need a backfill."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from zoneinfo import ZoneInfo

from svoi_pravila.application.ports.clock import Clock
from svoi_pravila.application.ports.llm_price_store import LlmPriceStore
from svoi_pravila.domain.llm_pricing import LlmPrice


@dataclass(frozen=True, slots=True)
class SetLlmPricePorts:
    """Collaborators for ``SetLlmPrice``."""

    store: LlmPriceStore
    clock: Clock
    timezone: str


@dataclass(frozen=True, slots=True)
class SetLlmPriceResult:
    """Closed range of days whose stored aggregates still carry the old price."""

    backfill_from: date | None
    backfill_to: date | None


class SetLlmPrice:
    """Upsert a price row; aggregates of already computed days keep the old price."""

    def __init__(self, ports: SetLlmPricePorts) -> None:
        self._store = ports.store
        self._clock = ports.clock
        self._timezone = ports.timezone

    async def execute(self, price: LlmPrice) -> SetLlmPriceResult:
        """Store ``price`` and return the days a backfill must recompute (none if future)."""
        await self._store.upsert_price(price)
        later = [
            item.valid_from
            for item in await self._store.list_prices()
            if item.model == price.model and item.valid_from > price.valid_from
        ]
        last_day = self._clock.now().astimezone(ZoneInfo(self._timezone)).date() - timedelta(days=1)
        if later:
            last_day = min(last_day, min(later) - timedelta(days=1))
        if price.valid_from > last_day:
            return SetLlmPriceResult(backfill_from=None, backfill_to=None)
        return SetLlmPriceResult(backfill_from=price.valid_from, backfill_to=last_day)
