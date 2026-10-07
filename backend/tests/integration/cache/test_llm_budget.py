"""ValkeyLlmBudget rebuild from sum port and concurrent SET NX."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
from redis.asyncio import Redis

from svoi_pravila.adapters.cache.llm_budget import ValkeyLlmBudget, ValkeyLlmBudgetConfig
from svoi_pravila.application.ports.llm_budget import BudgetExhausted, BudgetOk
from svoi_pravila.domain.product_day import product_day

_TZ = "Europe/Moscow"
_DAY = product_day(datetime.now(UTC), _TZ)
_SUM = 4_200


class _FixedSum:
    def __init__(self, total: int = _SUM) -> None:
        self.total = total
        self.calls = 0

    async def sum_for_day(self, day: object) -> int:
        assert day == _DAY
        self.calls += 1
        return self.total


def _budget(client: Redis, sums: _FixedSum, *, budget: int = 20_000) -> ValkeyLlmBudget:
    return ValkeyLlmBudget(
        client,
        config=ValkeyLlmBudgetConfig(budget=budget, timezone=_TZ),
        sums=sums,
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


@pytest.mark.integration
async def test_llm_budget_add_after_missing_key_does_not_double_count(
    valkey_db15: Redis,
) -> None:
    """Persist-then-add with a missing key rebuilds once; no second INCRBY."""
    event_tokens = 350
    sums = _FixedSum(total=event_tokens)
    budget = _budget(valkey_db15, sums)
    key = f"llm_budget:{_DAY.isoformat()}"
    await valkey_db15.delete(key)

    await budget.add(_DAY, event_tokens)
    assert int(await valkey_db15.get(key)) == event_tokens
    assert sums.calls == 1


@pytest.mark.integration
async def test_llm_budget_rebuild_overcount_bound_by_inflight_adds(
    valkey_db15: Redis,
) -> None:
    """In-flight adds during rebuild may over-count; bound is the in-flight count."""
    base = 1_000
    inflight = 8
    per_add = 25
    sums = _FixedSum(total=base)
    budget = _budget(valkey_db15, sums, budget=1_000_000)
    key = f"llm_budget:{_DAY.isoformat()}"
    await valkey_db15.delete(key)

    async def _add_one() -> None:
        await budget.add(_DAY, per_add)

    await asyncio.gather(*[_add_one() for _ in range(inflight)])
    spent = int(await valkey_db15.get(key))
    # Exact once each via INCR when a peer's SET already won, or rebuild-only:
    # over-count relative to base is at most inflight * per_add.
    assert spent >= base
    assert spent <= base + inflight * per_add
