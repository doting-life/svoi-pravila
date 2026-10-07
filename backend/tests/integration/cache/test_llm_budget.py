"""ValkeyLlmBudget rebuild from sum port and concurrent SET NX."""

from __future__ import annotations

import asyncio
from datetime import date

import pytest
from redis.asyncio import Redis

from svoi_pravila.adapters.cache.concurrency import ValkeyConcurrencyGuard
from svoi_pravila.adapters.cache.llm_budget import ValkeyLlmBudget, ValkeyLlmBudgetConfig
from svoi_pravila.application.ports.llm_budget import BudgetExhausted, BudgetOk

_DAY = date(2026, 3, 15)
_TZ = "Europe/Moscow"
_SUM = 4_200


class _FixedSum:
    def __init__(self, total: int = _SUM) -> None:
        self.total = total
        self.calls = 0

    async def sum_for_day(self, day: date) -> int:
        assert day == _DAY
        self.calls += 1
        return self.total


def _budget(client: Redis, sums: _FixedSum, *, budget: int = 20_000) -> ValkeyLlmBudget:
    return ValkeyLlmBudget(
        client,
        config=ValkeyLlmBudgetConfig(budget=budget, timezone=_TZ),
        sums=sums,
        guard=ValkeyConcurrencyGuard(client),
    )


@pytest.mark.integration
async def test_llm_budget_rebuild_equals_sum_after_delete(valkey_db15: Redis) -> None:
    sums = _FixedSum()
    budget = _budget(valkey_db15, sums)
    key = f"llm_budget:{_DAY.isoformat()}"
    await valkey_db15.set(key, "99")
    assert await budget.check(_DAY) == BudgetOk()
    assert await valkey_db15.get(key) == "99"
    assert sums.calls == 0

    await valkey_db15.delete(key)
    assert await budget.check(_DAY) == BudgetOk()
    rebuilt = await valkey_db15.get(key)
    assert rebuilt is not None
    assert int(rebuilt) == _SUM
    assert sums.calls == 1

    await valkey_db15.set(key, str(20_000))
    exhausted = await budget.check(_DAY)
    assert isinstance(exhausted, BudgetExhausted)


@pytest.mark.integration
async def test_llm_budget_concurrent_rebuilds_one_set_wins(valkey_db15: Redis) -> None:
    sums = _FixedSum()
    budget = _budget(valkey_db15, sums)
    key = f"llm_budget:{_DAY.isoformat()}"
    await valkey_db15.delete(key)

    results = await asyncio.gather(*[budget.check(_DAY) for _ in range(40)])
    assert all(item == BudgetOk() for item in results)
    rebuilt = await valkey_db15.get(key)
    assert rebuilt is not None
    assert int(rebuilt) == _SUM
    assert sums.calls >= 1
    assert sums.calls <= 40
