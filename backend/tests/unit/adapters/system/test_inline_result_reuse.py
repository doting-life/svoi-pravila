"""Unit tests for InProcessInlineResultReuse."""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from tests.fakes.call_later import FakeCallLater, FakeCallLaterHandle
from tests.fakes.clock import FakeClock

from svoi_pravila.adapters.system.inline_result_reuse import (
    InProcessInlineResultReuse,
    _Entry,
    _Flight,
)
from svoi_pravila.application.errors import GenerationUnavailable, UnavailableKind
from svoi_pravila.application.inline_reuse_status import InlineReuseStatus
from svoi_pravila.application.ports.generation import SafetyVerdict, TokenUsage, Variant
from svoi_pravila.application.ports.inline_result_reuse import (
    InlineReuseValue,
    ProduceInlineReuse,
    ReuseFailed,
    ReuseSucceeded,
)
from svoi_pravila.domain.enums import Firmness, UsageScenario


def _value(
    text: str = "ok-variant", *, safety: SafetyVerdict = SafetyVerdict.OK
) -> InlineReuseValue:
    return InlineReuseValue(
        scenario=UsageScenario.SOFTEN,
        variants=(Variant(text=text, firmness=Firmness.GENTLE),),
        safety=safety,
        applied_rules=(),
    )


def _reuse(
    clock: FakeClock, **kwargs: float | int
) -> tuple[InProcessInlineResultReuse, FakeCallLater]:
    later = FakeCallLater(clock)
    reuse = InProcessInlineResultReuse(
        clock,
        ttl_seconds=float(kwargs.get("ttl_seconds", 30.0)),
        max_entries=int(kwargs.get("max_entries", 10)),
        max_per_user=int(kwargs.get("max_per_user", 4)),
        call_later=later,
    )
    return reuse, later


@pytest.mark.unit
async def test_hit_after_miss_and_ttl_expiry() -> None:
    clock = FakeClock()
    reuse, _later = _reuse(clock)
    calls = 0

    async def produce() -> InlineReuseValue:
        nonlocal calls
        calls += 1
        return _value()

    first = await reuse.resolve("k1", "u1", produce)
    second = await reuse.resolve("k1", "u1", produce)
    assert isinstance(first, ReuseSucceeded)
    assert isinstance(second, ReuseSucceeded)
    assert first.status is InlineReuseStatus.MISS
    assert second.status is InlineReuseStatus.HIT
    assert calls == 1
    clock.advance(timedelta(seconds=31))
    third = await reuse.resolve("k1", "u1", produce)
    assert isinstance(third, ReuseSucceeded)
    assert third.status is InlineReuseStatus.MISS
    assert calls == 2


@pytest.mark.unit
async def test_ttl_idle_eviction_clears_stats_without_resolve() -> None:
    """A: idle TTL eviction removes the entry without a further resolve."""
    clock = FakeClock()
    reuse, later = _reuse(clock, ttl_seconds=30.0)
    await reuse.resolve("k1", "u1", _producer("a"))
    assert reuse.stats().entries == 1
    assert reuse.stats().users == 1
    clock.advance(timedelta(seconds=31))
    later.fire_due()
    await asyncio.sleep(0)
    assert "k1" not in reuse._entries
    assert reuse.stats().entries == 0
    assert reuse.stats().users == 0
    assert reuse.stats().flights == 0


@pytest.mark.unit
async def test_join_shares_one_produce() -> None:
    clock = FakeClock()
    reuse, _later = _reuse(clock)
    gate = asyncio.Event()
    calls = 0

    async def produce() -> InlineReuseValue:
        nonlocal calls
        calls += 1
        await gate.wait()
        return _value("shared")

    t1 = asyncio.create_task(reuse.resolve("k", "u", produce))
    await asyncio.sleep(0)
    t2 = asyncio.create_task(reuse.resolve("k", "u", produce))
    await asyncio.sleep(0)
    gate.set()
    r1, r2 = await asyncio.gather(t1, t2)
    assert calls == 1
    assert {r1.status, r2.status} == {InlineReuseStatus.MISS, InlineReuseStatus.JOIN}
    assert isinstance(r1, ReuseSucceeded) and isinstance(r2, ReuseSucceeded)
    assert r1.value.variants == r2.value.variants


@pytest.mark.unit
async def test_error_propagates_to_joiners_and_is_not_stored() -> None:
    clock = FakeClock()
    reuse, _later = _reuse(clock)
    gate = asyncio.Event()
    calls = 0

    async def produce() -> InlineReuseValue:
        nonlocal calls
        calls += 1
        await gate.wait()
        raise GenerationUnavailable(
            UnavailableKind.TIMEOUT,
            usage=TokenUsage(),
            attempts=1,
            model="fake",
            prompt_version="soften@v1",
        )

    t1 = asyncio.create_task(reuse.resolve("k", "u", produce))
    await asyncio.sleep(0)
    t2 = asyncio.create_task(reuse.resolve("k", "u", produce))
    await asyncio.sleep(0)
    gate.set()
    r1, r2 = await asyncio.gather(t1, t2)
    assert calls == 1
    assert isinstance(r1, ReuseFailed) and isinstance(r2, ReuseFailed)
    assert isinstance(r1.error, GenerationUnavailable)
    assert isinstance(r2.error, GenerationUnavailable)

    async def ok() -> InlineReuseValue:
        nonlocal calls
        calls += 1
        return _value()

    again = await reuse.resolve("k", "u", ok)
    assert isinstance(again, ReuseSucceeded)
    assert again.status is InlineReuseStatus.MISS
    assert calls == 2


@pytest.mark.unit
async def test_non_ok_safety_not_stored() -> None:
    clock = FakeClock()
    reuse, _later = _reuse(clock)
    calls = 0

    async def produce() -> InlineReuseValue:
        nonlocal calls
        calls += 1
        return _value(safety=SafetyVerdict.REFUSE_MANIPULATION)

    first = await reuse.resolve("k", "u", produce)
    second = await reuse.resolve("k", "u", produce)
    assert first.status is InlineReuseStatus.MISS
    assert second.status is InlineReuseStatus.MISS
    assert calls == 2
    assert reuse.stats().entries == 0
    assert reuse.stats().users == 0


@pytest.mark.unit
async def test_waiter_cancel_does_not_cancel_shared_generation() -> None:
    clock = FakeClock()
    reuse, _later = _reuse(clock)
    gate = asyncio.Event()
    finished = asyncio.Event()

    async def produce() -> InlineReuseValue:
        await gate.wait()
        finished.set()
        return _value("survived")

    waiter = asyncio.create_task(reuse.resolve("k", "u", produce))
    await asyncio.sleep(0)
    assert reuse.tasks
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    assert not finished.is_set()
    gate.set()
    await asyncio.wait_for(finished.wait(), timeout=1.0)
    hit = await reuse.resolve("k", "u", produce)
    assert isinstance(hit, ReuseSucceeded)
    assert hit.status is InlineReuseStatus.HIT
    assert hit.value.variants[0].text == "survived"


def _producer(text: str, calls: list[int] | None = None) -> ProduceInlineReuse:
    async def produce() -> InlineReuseValue:
        if calls is not None:
            calls.append(1)
        return _value(text)

    return produce


@pytest.mark.unit
async def test_per_user_bound_evicts_oldest() -> None:
    clock = FakeClock()
    reuse, _later = _reuse(clock, max_entries=100, max_per_user=2)
    calls: list[int] = []
    await reuse.resolve("a", "u1", _producer("a", calls))
    clock.advance(timedelta(seconds=1))
    await reuse.resolve("b", "u1", _producer("b", calls))
    clock.advance(timedelta(seconds=1))
    await reuse.resolve("c", "u1", _producer("c", calls))
    miss_a = await reuse.resolve("a", "u1", _producer("a2", calls))
    hit_c = await reuse.resolve("c", "u1", _producer("c2", calls))
    assert miss_a.status is InlineReuseStatus.MISS
    assert hit_c.status is InlineReuseStatus.HIT


@pytest.mark.unit
async def test_global_bound_evicts_oldest() -> None:
    clock = FakeClock()
    reuse, _later = _reuse(clock, max_entries=2, max_per_user=4)
    await reuse.resolve("a", "u1", _producer("a"))
    clock.advance(timedelta(seconds=1))
    await reuse.resolve("b", "u2", _producer("b"))
    clock.advance(timedelta(seconds=1))
    await reuse.resolve("c", "u3", _producer("c"))
    miss_a = await reuse.resolve("a", "u1", _producer("a2"))
    hit_c = await reuse.resolve("c", "u3", _producer("c2"))
    assert miss_a.status is InlineReuseStatus.MISS
    assert hit_c.status is InlineReuseStatus.HIT
    assert "entries=2" in repr(reuse)


@pytest.mark.unit
async def test_forget_drops_hits_and_clears_user_stats() -> None:
    """B: forget clears stored entries and user keys from stats."""
    clock = FakeClock()
    reuse, _later = _reuse(clock)
    calls = 0

    async def produce() -> InlineReuseValue:
        nonlocal calls
        calls += 1
        return _value()

    await reuse.resolve("k", "u1", produce)
    assert reuse.stats().users == 1
    reuse.forget("u1")
    assert reuse.stats().entries == 0
    assert reuse.stats().users == 0
    again = await reuse.resolve("k", "u1", produce)
    assert again.status is InlineReuseStatus.MISS
    assert calls == 2


@pytest.mark.unit
async def test_forget_during_flight_skips_store_and_stats_return_to_zero() -> None:
    """B: forget mid-flight prevents store; stats are zero after the flight ends."""
    clock = FakeClock()
    reuse, _later = _reuse(clock)
    gate = asyncio.Event()

    async def produce() -> InlineReuseValue:
        await gate.wait()
        return _value()

    task = asyncio.create_task(reuse.resolve("k", "u1", produce))
    await asyncio.sleep(0)
    assert reuse.stats().flights == 1
    reuse.forget("u1")
    gate.set()
    await task
    assert reuse.stats().entries == 0
    assert reuse.stats().users == 0
    assert reuse.stats().flights == 0
    calls = 0

    async def again() -> InlineReuseValue:
        nonlocal calls
        calls += 1
        return _value("new")

    miss = await reuse.resolve("k", "u1", again)
    assert miss.status is InlineReuseStatus.MISS
    assert calls == 1


@pytest.mark.unit
async def test_forget_leaves_other_users_in_flight_store_flag() -> None:
    clock = FakeClock()
    reuse, _later = _reuse(clock)
    gate = asyncio.Event()

    async def produce() -> InlineReuseValue:
        await gate.wait()
        return _value("other")

    task = asyncio.create_task(reuse.resolve("k2", "u2", produce))
    await asyncio.sleep(0)
    reuse.forget("u1")
    flight = next(iter(reuse._flights.values()))
    assert flight.user_key == "u2"
    assert flight.store is True
    gate.set()
    await task
    hit = await reuse.resolve("k2", "u2", _producer("ignored"))
    assert isinstance(hit, ReuseSucceeded)
    assert hit.status is InlineReuseStatus.HIT


@pytest.mark.unit
def test_repr_exposes_no_text() -> None:
    clock = FakeClock()
    reuse, _later = _reuse(clock)
    text = "SECRET_DRAFT_TEXT_SHOULD_NOT_APPEAR"
    reuse._store("keyhash", "user", _value(text))
    blob = repr(reuse) + repr(next(iter(reuse._entries.values())))
    assert text not in blob
    assert "SECRET" not in blob


@pytest.mark.unit
def test_invalid_bounds() -> None:
    clock = FakeClock()
    with pytest.raises(ValueError):
        InProcessInlineResultReuse(clock, ttl_seconds=1.0, max_entries=0)
    with pytest.raises(ValueError):
        InProcessInlineResultReuse(clock, ttl_seconds=1.0, max_entries=1, max_per_user=0)


@pytest.mark.unit
def test_store_replaces_existing_key() -> None:
    clock = FakeClock()
    reuse, later = _reuse(clock)
    reuse._store("k", "u", _value("a"))
    first_timer = reuse._entries["k"].timer
    assert isinstance(first_timer, FakeCallLaterHandle)
    reuse._store("k", "u", _value("b"))
    assert reuse._entries["k"].value.variants[0].text == "b"
    assert reuse._user_order["u"] == ["k"]
    assert first_timer.cancelled is True
    assert later._pending  # replacement scheduled a new timer


@pytest.mark.unit
def test_evict_helpers_cover_empty_and_inconsistent_state() -> None:
    clock = FakeClock()
    reuse, _later = _reuse(clock)
    reuse._evict_oldest_global()
    reuse._evict_key("missing")
    reuse._entries["orphan"] = _Entry(value=_value("x"), stored_at=0.0, user_key="u")
    reuse._evict_key("orphan")
    reuse._entries["k"] = _Entry(value=_value("y"), stored_at=0.0, user_key="u")
    reuse._user_order["u"] = ["other"]
    reuse._evict_key("k")
    assert "k" not in reuse._entries


@pytest.mark.unit
def test_expire_without_running_loop_is_noop() -> None:
    clock = FakeClock()
    reuse, later = _reuse(clock, ttl_seconds=1.0)
    reuse._store("k", "u", _value())
    clock.advance(timedelta(seconds=2))
    later.fire_due()
    assert "k" in reuse._entries


@pytest.mark.unit
async def test_await_flight_handles_cancelled_produce_task() -> None:
    clock = FakeClock()
    reuse, _later = _reuse(clock)
    gate = asyncio.Event()

    async def produce() -> InlineReuseValue:
        await gate.wait()
        return _value()

    waiter = asyncio.create_task(reuse.resolve("k", "u", produce))
    await asyncio.sleep(0)
    flight = next(iter(reuse.tasks))
    flight.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter


@pytest.mark.unit
async def test_await_flight_relay_ignores_when_local_done() -> None:
    clock = FakeClock()
    reuse, _later = _reuse(clock)
    gate = asyncio.Event()

    async def slow() -> InlineReuseValue:
        await gate.wait()
        return _value("late")

    flight = asyncio.create_task(slow())
    waiter = asyncio.create_task(reuse._await_flight(flight))
    await asyncio.sleep(0)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    gate.set()
    await flight


@pytest.mark.unit
async def test_flight_slot_replaced_before_cleanup() -> None:
    clock = FakeClock()
    reuse, _later = _reuse(clock)

    async def produce() -> InlineReuseValue:
        async def other() -> InlineReuseValue:
            return _value("other")

        reuse._flights["k"] = _Flight(task=asyncio.create_task(other()), user_key="u")
        return _value("mine")

    resolution = await reuse.resolve("k", "u", produce)
    assert isinstance(resolution, ReuseSucceeded)
    assert resolution.status is InlineReuseStatus.MISS
    assert resolution.value.variants[0].text == "mine"


@pytest.mark.unit
async def test_default_call_later_uses_running_loop() -> None:
    clock = FakeClock()
    reuse = InProcessInlineResultReuse(clock, ttl_seconds=0.01, max_entries=10)
    await reuse.resolve("k", "u", _producer("a"))
    assert reuse.stats().entries == 1
    await asyncio.sleep(0.05)
    assert reuse.stats().entries == 0
    assert reuse.stats().users == 0
