"""Port for the LLM price table (roubles per 1000 billable tokens)."""

from __future__ import annotations

from typing import Protocol

from svoi_pravila.domain.llm_pricing import LlmPrice


class LlmPriceStore(Protocol):
    """Read and upsert ``llm_prices`` rows."""

    async def list_prices(self) -> tuple[LlmPrice, ...]:
        """All prices ordered by model, then ``valid_from``."""
        ...

    async def upsert_price(self, price: LlmPrice) -> None:
        """Insert or replace the row keyed by ``(model, valid_from)``."""
        ...
