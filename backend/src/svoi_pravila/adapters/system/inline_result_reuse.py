"""In-process bounded inline result reuse (hit / join / miss)."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass

from svoi_pravila.application.errors import ApplicationError
from svoi_pravila.application.ports.generation import SafetyVerdict
from svoi_pravila.application.ports.inline_result_reuse import (
    InlineReuseResolution,
    InlineReuseStatus,
    InlineReuseValue,
    ProduceInlineReuse,
)
from svoi_pravila.application.ports.monotonic import MonotonicClock

_MAX_PER_USER = 4


@dataclass(slots=True)
class _Entry:
    value: InlineReuseValue
    stored_at: float
    user_key: str

    def __repr__(self) -> str:
        return f"_Entry(stored_at={self.stored_at!r}, user_key_len={len(self.user_key)})"


class InProcessInlineResultReuse:
    """Process-memory reuse with TTL, global and per-user bounds, and joiners."""

    def __init__(
        self,
        monotonic: MonotonicClock,
        *,
        ttl_seconds: float,
        max_entries: int,
        max_per_user: int = _MAX_PER_USER,
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
        self._entries: dict[str, _Entry] = {}
        self._user_order: dict[str, list[str]] = defaultdict(list)
        self._forget_gen: dict[str, int] = defaultdict(int)
        self._flights: dict[str, asyncio.Task[InlineReuseValue]] = {}
        self._tasks: set[asyncio.Task[InlineReuseValue]] = set()
        self._lock = asyncio.Lock()

    @property
    def tasks(self) -> set[asyncio.Task[InlineReuseValue]]:
        """In-flight produce tasks for shutdown draining."""
        return self._tasks

    def __repr__(self) -> str:
        return (
            "InProcessInlineResultReuse("
            f"entries={len(self._entries)}, "
            f"flights={len(self._flights)}, "
            f"ttl_seconds={self._ttl_seconds}, "
            f"max_entries={self._max_entries})"
        )

    def forget(self, user_key: str) -> None:
        """Drop stored entries for ``user_key`` and invalidate in-flight stores."""
        self._forget_gen[user_key] = self._forget_gen[user_key] + 1
        for key in list(self._user_order.get(user_key, ())):
            self._entries.pop(key, None)
        self._user_order.pop(user_key, None)

    async def resolve(
        self,
        key: str,
        user_key: str,
        produce: ProduceInlineReuse,
    ) -> InlineReuseResolution:
        """Hit a fresh OK entry, join an in-flight produce, or run ``produce``."""
        async with self._lock:
            hit = self._lookup(key)
            if hit is not None:
                return InlineReuseResolution(
                    status=InlineReuseStatus.HIT,
                    value=hit,
                    error=None,
                )
            existing = self._flights.get(key)
            if existing is not None:
                task = existing
                status = InlineReuseStatus.JOIN
            else:
                forget_gen = self._forget_gen[user_key]
                task = asyncio.create_task(
                    self._run_produce(key, user_key, forget_gen, produce),
                    name="inline-reuse-produce",
                )
                self._flights[key] = task
                self._tasks.add(task)
                task.add_done_callback(self._on_task_done)
                status = InlineReuseStatus.MISS

        try:
            value = await self._await_flight(task)
        except ApplicationError as exc:
            return InlineReuseResolution(status=status, value=None, error=exc)
        return InlineReuseResolution(status=status, value=value, error=None)

    async def _await_flight(self, task: asyncio.Task[InlineReuseValue]) -> InlineReuseValue:
        """Await ``task`` without linking waiter cancellation to the shared produce."""
        loop = asyncio.get_running_loop()
        local: asyncio.Future[InlineReuseValue] = loop.create_future()

        def _relay(done: asyncio.Task[InlineReuseValue]) -> None:
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
        forget_gen: int,
        produce: ProduceInlineReuse,
    ) -> InlineReuseValue:
        value: InlineReuseValue | None = None
        try:
            value = await produce()
            return value
        finally:
            async with self._lock:
                if self._flights.get(key) is asyncio.current_task():
                    del self._flights[key]
                if (
                    value is not None
                    and value.safety is SafetyVerdict.OK
                    and self._forget_gen[user_key] == forget_gen
                ):
                    self._store(key, user_key, value)

    def _on_task_done(self, task: asyncio.Task[InlineReuseValue]) -> None:
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
        self._entries[key] = _Entry(value=value, stored_at=now, user_key=user_key)
        self._user_order[user_key].append(key)
        while len(self._user_order[user_key]) > self._max_per_user:
            oldest = self._user_order[user_key][0]
            self._evict_key(oldest)
        while len(self._entries) > self._max_entries:
            self._evict_oldest_global()

    def _evict_oldest_global(self) -> None:
        if not self._entries:
            return
        oldest_key = min(self._entries, key=lambda k: self._entries[k].stored_at)
        self._evict_key(oldest_key)

    def _evict_key(self, key: str) -> None:
        entry = self._entries.pop(key, None)
        if entry is None:
            return
        order = self._user_order.get(entry.user_key)
        if order is None:
            return
        try:
            order.remove(key)
        except ValueError:
            return
        if not order:
            del self._user_order[entry.user_key]
