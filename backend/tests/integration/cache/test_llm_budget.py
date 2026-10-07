"""ValkeyLlmBudget rebuild from sum port and concurrent SET NX."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from math import floor
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from redis.asyncio import Redis
from tests.fakes.clock import FakeClock

from svoi_pravila.adapters.cache.llm_budget import ValkeyLlmBudget, ValkeyLlmBudgetConfig
from svoi_pravila.application.ports.clock import Clock
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
        _ = day
        self.calls += 1
        return self.total


def _budget(
    client: Redis,
    sums: _FixedSum,
    *,
    budget: int = 20_000,
    timezone: str = _TZ,
    spike_share: float = 0.25,
    clock: Clock | None = None,
) -> ValkeyLlmBudget:
    return ValkeyLlmBudget(
        client,
        config=ValkeyLlmBudgetConfig(
            budget=budget,
            timezone=timezone,
            hourly_spike_share=spike_share,
        ),
        sums=sums,
        clock=FakeClock() if clock is None else clock,
    )


def _spike_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [event for event in events if event.get("event") == "llm_spend_spike"]


def _near_now_clock(*, minute: int = 30) -> FakeClock:
    """Clock near wall-clock now so Valkey EXPIREAT stays in the future."""
    now = datetime.now(UTC).replace(minute=minute, second=0, microsecond=0)
    return FakeClock(now)


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
    rebuilt = await valkey_db15.get(key)
    assert rebuilt is not None
    assert int(rebuilt) == event_tokens
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
    raw = await valkey_db15.get(key)
    assert raw is not None
    spent = int(raw)
    assert spent >= base
    assert spent <= base + inflight * per_add


@pytest.mark.integration
async def test_llm_spend_spike_exactly_one_crossing_under_concurrency(
    valkey_db15: Redis,
    capture_log_events: Callable[[], list[dict[str, Any]]],
) -> None:
    budget_tokens = 10_000
    share = 0.25
    threshold = floor(share * budget_tokens)
    per_add = 100
    clock = _near_now_clock()
    day = product_day(clock.now(), _TZ)
    budget = _budget(
        valkey_db15,
        _FixedSum(total=0),
        budget=budget_tokens,
        spike_share=share,
        clock=clock,
    )
    key = f"llm_budget:{day.isoformat()}"
    await valkey_db15.delete(key)
    await valkey_db15.set(key, "0")
    hour = clock.now().astimezone(ZoneInfo(_TZ)).hour
    hour_key = f"llm_budget:hour:{day.isoformat()}:{hour:02d}"
    await valkey_db15.delete(hour_key)

    await asyncio.gather(*[budget.add(day, per_add) for _ in range(50)])

    assert len(_spike_events(capture_log_events())) == 1
    hour_raw = await valkey_db15.get(hour_key)
    assert hour_raw is not None
    assert int(hour_raw) == 50 * per_add
    assert int(hour_raw) >= threshold


@pytest.mark.integration
async def test_llm_spend_spike_none_below_threshold(
    valkey_db15: Redis,
    capture_log_events: Callable[[], list[dict[str, Any]]],
) -> None:
    budget_tokens = 10_000
    share = 0.25
    threshold = floor(share * budget_tokens)
    clock = _near_now_clock()
    day = product_day(clock.now(), _TZ)
    budget = _budget(
        valkey_db15,
        _FixedSum(total=0),
        budget=budget_tokens,
        spike_share=share,
        clock=clock,
    )
    key = f"llm_budget:{day.isoformat()}"
    await valkey_db15.delete(key)
    await valkey_db15.set(key, "0")
    hour = clock.now().astimezone(ZoneInfo(_TZ)).hour
    hour_key = f"llm_budget:hour:{day.isoformat()}:{hour:02d}"
    await valkey_db15.delete(hour_key)

    await budget.add(day, threshold - 1)

    assert _spike_events(capture_log_events()) == []
    assert int(await valkey_db15.get(hour_key) or b"0") == threshold - 1


@pytest.mark.integration
async def test_llm_spend_spike_resets_on_new_hour(
    valkey_db15: Redis,
    capture_log_events: Callable[[], list[dict[str, Any]]],
) -> None:
    budget_tokens = 10_000
    share = 0.25
    threshold = floor(share * budget_tokens)
    # Stay two hours before local midnight so +1h stays on the same product day.
    local_now = datetime.now(UTC).astimezone(ZoneInfo(_TZ))
    start_local = local_now.replace(
        hour=min(local_now.hour, 21),
        minute=30,
        second=0,
        microsecond=0,
    )
    clock = FakeClock(start_local.astimezone(UTC))
    day = product_day(clock.now(), _TZ)
    budget = _budget(
        valkey_db15,
        _FixedSum(total=0),
        budget=budget_tokens,
        spike_share=share,
        clock=clock,
    )
    key = f"llm_budget:{day.isoformat()}"
    await valkey_db15.delete(key)
    await valkey_db15.set(key, "0")

    await budget.add(day, threshold)
    assert len(_spike_events(capture_log_events())) == 1

    clock.advance(timedelta(hours=1))
    await budget.add(day, threshold)
    assert len(_spike_events(capture_log_events())) == 2
    h1 = clock.now().astimezone(ZoneInfo(_TZ)).hour
    assert await valkey_db15.get(f"llm_budget:hour:{day.isoformat()}:{h1:02d}") is not None
