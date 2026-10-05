"""In-process bounded inline result reuse (hit / join / miss)."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from svoi_pravila.application.inline_reuse_status import InlineReuseStatus
from svoi_pravila.application.ports.generation import SafetyVerdict
from svoi_pravila.application.ports.inline_result_reuse import (
    InlineReuseValue,
    ProduceInlineReuse,
    ProduceOutcome,
    ReuseFailed,
    ReuseSucceeded,
)
from svoi_pravila.application.ports.monotonic import MonotonicClock

_MAX_PER_USER = 4


class CancelHandle(Protocol):
    """Handle returned by ``call_later`` that can cancel the callback."""

    def cancel(self) -> object:
        """Cancel the scheduled callback if it has not run."""
        ...


CallLater = Callable[[float, Callable[[], None]], CancelHandle]


@dataclass(frozen=True, slots=True)
class InlineReuseStats:
    """C0 counts for in-process reuse state (no keys or text)."""

    entries: int
    users: int
    flights: int


@dataclass(slots=True)
class _Entry:
    value: InlineReuseValue
    stored_at: float
    user_key: str
    timer: CancelHandle | None = None

    def __repr__(self) -> str:
        return f"_Entry(stored_at={self.stored_at!r}, user_key_len={len(self.user_key)})"


@dataclass(slots=True)
class _Flight:
    task: asyncio.Task[ProduceOutcome]
    user_key: str
    store: bool = True


class InProcessInlineResultReuse:
    """Process-memory reuse with TTL timers, bounds, and joiners."""

    def __init__(
        self,
        monotonic: MonotonicClock,
        *,
        ttl_seconds: float,
        max_entries: int,
        max_per_user: int = _MAX_PER_USER,
        call_later: CallLater | None = None,
    ) -> None:
        if max_entries < 1:
            msg = "max_entries must be >= 1"
            raise ValueError(msg)
        if max_per_user < 1:
            msg = "max_per_user must be >= 1"
            raise ValueError(msg)
        self._monotonic = monotonic
        self._ttl_seconds = ttl_seconds
        self._max_entries = max_entries
        self._max_per_user = max_per_user
        self._call_later = call_later
        self._entries: dict[str, _Entry] = {}
        self._user_order: dict[str, list[str]] = {}
        self._flights: dict[str, _Flight] = {}
        self._tasks: set[asyncio.Task[ProduceOutcome]] = set()

    @property
    def tasks(self) -> set[asyncio.Task[ProduceOutcome]]:
        """In-flight produce tasks for shutdown draining."""
        return self._tasks

    def stats(self) -> InlineReuseStats:
        """Return C0 counts of entries, distinct users, and in-flight produces."""
        return InlineReuseStats(
            entries=len(self._entries),
            users=len(self._user_order),
            flights=len(self._flights),
        )

    def __repr__(self) -> str:
        return (
            "InProcessInlineResultReuse("
            f"entries={len(self._entries)}, "
            f"flights={len(self._flights)}, "
            f"ttl_seconds={self._ttl_seconds}, "
            f"max_entries={self._max_entries})"
        )

    def forget(self, user_key: str) -> None:
        """Drop stored entries for ``user_key`` and mark in-flight produces no-store."""
        for key in list(self._user_order.get(user_key, ())):
            self._evict_key(key)
        self._user_order.pop(user_key, None)
        for flight in self._flights.values():
            if flight.user_key == user_key:
                flight.store = False

    async def resolve(
        self,
        key: str,
        user_key: str,
        produce: ProduceInlineReuse,
    ) -> ReuseSucceeded | ReuseFailed:
        """Hit a fresh OK entry, join an in-flight produce, or run ``produce``."""
        hit = self._lookup(key)
        if hit is not None:
            return ReuseSucceeded(status=InlineReuseStatus.HIT, value=hit)
        existing = self._flights.get(key)
        if existing is not None:
            task = existing.task
            status = InlineReuseStatus.JOIN
        else:
            task = asyncio.create_task(
                self._run_produce(key, user_key, produce),
                name="inline-reuse-produce",
            )
            self._flights[key] = _Flight(task=task, user_key=user_key, store=True)
            self._tasks.add(task)
            task.add_done_callback(self._on_task_done)
            status = InlineReuseStatus.MISS

        outcome = await self._await_flight(task)
        if isinstance(outcome, InlineReuseValue):
            return ReuseSucceeded(status=status, value=outcome)
        return ReuseFailed(status=status, error=outcome)

    async def _await_flight(self, task: asyncio.Task[ProduceOutcome]) -> ProduceOutcome:
        """Await ``task`` without linking waiter cancellation to the shared produce."""
        loop = asyncio.get_running_loop()
        local: asyncio.Future[ProduceOutcome] = loop.create_future()

        def _relay(done: asyncio.Task[ProduceOutcome]) -> None:
            task.remove_done_callback(_relay)
            if local.done():
                return
            if done.cancelled():
                local.cancel()
                return
            exc = done.exception()
            if exc is not None:
                local.set_exception(exc)
                return
            local.set_result(done.result())

        task.add_done_callback(_relay)
        return await local

    async def _run_produce(
        self,
        key: str,
        user_key: str,
        produce: ProduceInlineReuse,
    ) -> ProduceOutcome:
        outcome: ProduceOutcome | None = None
        try:
            outcome = await produce()
            return outcome
        finally:
            flight = self._flights.get(key)
            should_store = False
            if flight is not None and flight.task is asyncio.current_task():
                should_store = flight.store
                del self._flights[key]
            if (
                isinstance(outcome, InlineReuseValue)
                and outcome.safety is SafetyVerdict.OK
                and should_store
            ):
                self._store(key, user_key, outcome)

    def _on_task_done(self, task: asyncio.Task[ProduceOutcome]) -> None:
        self._tasks.discard(task)

    def _lookup(self, key: str) -> InlineReuseValue | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        age = self._monotonic.monotonic() - entry.stored_at
        if age >= self._ttl_seconds:
            self._evict_key(key)
            return None
        return entry.value

    def _store(self, key: str, user_key: str, value: InlineReuseValue) -> None:
        if key in self._entries:
            self._evict_key(key)
        now = self._monotonic.monotonic()
        timer = self._schedule_expiry(key)
        self._entries[key] = _Entry(value=value, stored_at=now, user_key=user_key, timer=timer)
        order = self._user_order.get(user_key)
        if order is None:
            order = []
            self._user_order[user_key] = order
        order.append(key)
        while len(order) > self._max_per_user:
            self._evict_key(order[0])
        while len(self._entries) > self._max_entries:
            self._evict_oldest_global()

    def _schedule_expiry(self, key: str) -> CancelHandle:
        def _callback() -> None:
            self._evict_key(key)

        if self._call_later is not None:
            return self._call_later(self._ttl_seconds, _callback)
        return asyncio.get_running_loop().call_later(self._ttl_seconds, _callback)

    def _evict_oldest_global(self) -> None:
        if not self._entries:
            return
        oldest_key = min(self._entries, key=lambda k: self._entries[k].stored_at)
        self._evict_key(oldest_key)

    def _evict_key(self, key: str) -> None:
        entry = self._entries.pop(key, None)
        if entry is None:
            return
        if entry.timer is not None:
            entry.timer.cancel()
            entry.timer = None
        order = self._user_order.get(entry.user_key)
        if order is None:
            return
        try:
            order.remove(key)
        except ValueError:
            return
        if not order:
            del self._user_order[entry.user_key]
