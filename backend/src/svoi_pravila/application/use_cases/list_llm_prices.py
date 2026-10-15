"""List the LLM price table."""

from __future__ import annotations

from svoi_pravila.application.ports.llm_price_store import LlmPriceStore
from svoi_pravila.domain.llm_pricing import LlmPrice


class ListLlmPrices:
    """All price rows ordered by model, then ``valid_from``."""

    def __init__(self, store: LlmPriceStore) -> None:
        self._store = store

    async def execute(self) -> tuple[LlmPrice, ...]:
        """Return every stored price row."""
        return await self._store.list_prices()
