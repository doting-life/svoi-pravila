"""Unit tests for InProcessInlineResultReuse."""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from tests.fakes.clock import FakeClock

from svoi_pravila.adapters.system.inline_result_reuse import (
    InProcessInlineResultReuse,
    _Entry,
)
from svoi_pravila.application.errors import GenerationUnavailable, UnavailableKind
from svoi_pravila.application.ports.generation import SafetyVerdict, TokenUsage, Variant
from svoi_pravila.application.ports.inline_result_reuse import (
    InlineReuseStatus,
    InlineReuseValue,
    ProduceInlineReuse,
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


@pytest.mark.unit
async def test_hit_after_miss_and_ttl_expiry() -> None:
    clock = FakeClock()
    reuse = InProcessInlineResultReuse(clock, ttl_seconds=30.0, max_entries=10)
    calls = 0

    async def produce() -> InlineReuseValue:
        nonlocal calls
        calls += 1
        return _value()

    first = await reuse.resolve("k1", "u1", produce)
    second = await reuse.resolve("k1", "u1", produce)
    assert first.status is InlineReuseStatus.MISS
    assert second.status is InlineReuseStatus.HIT
    assert calls == 1
    clock.advance(timedelta(seconds=31))
    third = await reuse.resolve("k1", "u1", produce)
    assert third.status is InlineReuseStatus.MISS
    assert calls == 2


@pytest.mark.unit
async def test_join_shares_one_produce() -> None:
    clock = FakeClock()
    reuse = InProcessInlineResultReuse(clock, ttl_seconds=30.0, max_entries=10)
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
    assert r1.value is not None and r2.value is not None
    assert r1.value.variants == r2.value.variants


@pytest.mark.unit
async def test_error_propagates_to_joiners_and_is_not_stored() -> None:
    clock = FakeClock()
    reuse = InProcessInlineResultReuse(clock, ttl_seconds=30.0, max_entries=10)
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
    assert r1.error is not None and r2.error is not None
    assert isinstance(r1.error, GenerationUnavailable)
    assert isinstance(r2.error, GenerationUnavailable)

    async def ok() -> InlineReuseValue:
        nonlocal calls
        calls += 1
        return _value()

    again = await reuse.resolve("k", "u", ok)
    assert again.status is InlineReuseStatus.MISS
    assert calls == 2


@pytest.mark.unit
async def test_non_ok_safety_not_stored() -> None:
    clock = FakeClock()
    reuse = InProcessInlineResultReuse(clock, ttl_seconds=30.0, max_entries=10)
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


@pytest.mark.unit
async def test_waiter_cancel_does_not_cancel_shared_generation() -> None:
    clock = FakeClock()
    reuse = InProcessInlineResultReuse(clock, ttl_seconds=30.0, max_entries=10)
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
    assert hit.status is InlineReuseStatus.HIT
    assert hit.value is not None
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
    reuse = InProcessInlineResultReuse(clock, ttl_seconds=30.0, max_entries=100, max_per_user=2)
    calls: list[int] = []
    await reuse.resolve("a", "u1", _producer("a", calls))
    clock.advance(timedelta(seconds=1))
    await reuse.resolve("b", "u1", _producer("b", calls))
    clock.advance(timedelta(seconds=1))
    await reuse.resolve("c", "u1", _producer("c", calls))
    # oldest a evicted by per-user cap
    miss_a = await reuse.resolve("a", "u1", _producer("a2", calls))
    hit_c = await reuse.resolve("c", "u1", _producer("c2", calls))
    assert miss_a.status is InlineReuseStatus.MISS
    assert hit_c.status is InlineReuseStatus.HIT


@pytest.mark.unit
async def test_global_bound_evicts_oldest() -> None:
    clock = FakeClock()
    reuse = InProcessInlineResultReuse(clock, ttl_seconds=30.0, max_entries=2, max_per_user=4)
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
async def test_forget_drops_hits() -> None:
    clock = FakeClock()
    reuse = InProcessInlineResultReuse(clock, ttl_seconds=30.0, max_entries=10)
    calls = 0

    async def produce() -> InlineReuseValue:
        nonlocal calls
        calls += 1
        return _value()

    await reuse.resolve("k", "u1", produce)
    reuse.forget("u1")
    again = await reuse.resolve("k", "u1", produce)
    assert again.status is InlineReuseStatus.MISS
    assert calls == 2


@pytest.mark.unit
async def test_forget_during_flight_skips_store() -> None:
    clock = FakeClock()
    reuse = InProcessInlineResultReuse(clock, ttl_seconds=30.0, max_entries=10)
    gate = asyncio.Event()

    async def produce() -> InlineReuseValue:
        await gate.wait()
        return _value()

    task = asyncio.create_task(reuse.resolve("k", "u1", produce))
    await asyncio.sleep(0)
    reuse.forget("u1")
    gate.set()
    await task
    calls = 0

    async def again() -> InlineReuseValue:
        nonlocal calls
        calls += 1
        return _value("new")

    miss = await reuse.resolve("k", "u1", again)
    assert miss.status is InlineReuseStatus.MISS
    assert calls == 1


@pytest.mark.unit
def test_repr_exposes_no_text() -> None:
    clock = FakeClock()
    reuse = InProcessInlineResultReuse(clock, ttl_seconds=30.0, max_entries=10)
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
    reuse = InProcessInlineResultReuse(clock, ttl_seconds=30.0, max_entries=10)
    reuse._store("k", "u", _value("a"))
    reuse._store("k", "u", _value("b"))
    assert reuse._entries["k"].value.variants[0].text == "b"
    assert reuse._user_order["u"] == ["k"]


@pytest.mark.unit
def test_evict_helpers_cover_empty_and_inconsistent_state() -> None:
    clock = FakeClock()
    reuse = InProcessInlineResultReuse(clock, ttl_seconds=30.0, max_entries=10)
    reuse._evict_oldest_global()
    reuse._evict_key("missing")
    reuse._entries["orphan"] = _Entry(value=_value("x"), stored_at=0.0, user_key="u")
    reuse._evict_key("orphan")
    reuse._entries["k"] = _Entry(value=_value("y"), stored_at=0.0, user_key="u")
    reuse._user_order["u"] = ["other"]
    reuse._evict_key("k")
    assert "k" not in reuse._entries


@pytest.mark.unit
async def test_await_flight_handles_cancelled_produce_task() -> None:
    clock = FakeClock()
    reuse = InProcessInlineResultReuse(clock, ttl_seconds=30.0, max_entries=10)
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
    reuse = InProcessInlineResultReuse(clock, ttl_seconds=30.0, max_entries=10)
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
    reuse = InProcessInlineResultReuse(clock, ttl_seconds=30.0, max_entries=10)

    async def produce() -> InlineReuseValue:
        async def other() -> InlineReuseValue:
            return _value("other")

        reuse._flights["k"] = asyncio.create_task(other())
        return _value("mine")

    resolution = await reuse.resolve("k", "u", produce)
    assert resolution.status is InlineReuseStatus.MISS
    assert resolution.value is not None
    assert resolution.value.variants[0].text == "mine"
