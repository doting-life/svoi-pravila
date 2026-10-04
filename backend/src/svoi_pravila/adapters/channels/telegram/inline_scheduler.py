"""In-process per-user inline query debounce and cancellation."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

import structlog
from aiogram import Bot
from aiogram.types import InlineQuery

from svoi_pravila.adapters.channels.telegram.sleeper import Sleeper

if TYPE_CHECKING:
    from svoi_pravila.adapters.channels.telegram.deps import TelegramDeps

logger = structlog.get_logger(__name__)

AnswerFn = Callable[[InlineQuery, Bot, "TelegramDeps"], Awaitable[None]]


@dataclass
class _Slot:
    seq: int
    task: asyncio.Task[None]


@dataclass(frozen=True, slots=True)
class _Job:
    user_id: int
    seq: int
    query: InlineQuery
    bot: Bot
    deps: TelegramDeps
    answer: AnswerFn


class InlineQueryCoordinator:
    """One generation in flight per user; unstarted queries are superseded."""

    def __init__(self, sleeper: Sleeper, *, debounce_seconds: float) -> None:
        self._sleeper = sleeper
        self._debounce_seconds = debounce_seconds
        self._slots: dict[int, _Slot] = {}
        self._in_flight: dict[int, asyncio.Task[None]] = {}
        self._waiting: set[tuple[int, int]] = set()
        self._tasks: set[asyncio.Task[None]] = set()

    @property
    def tasks(self) -> set[asyncio.Task[None]]:
        """In-flight debounce/generation tasks for shutdown."""
        return self._tasks

    def submit(
        self,
        query: InlineQuery,
        bot: Bot,
        deps: TelegramDeps,
        answer: AnswerFn,
    ) -> None:
        """Schedule debounce then ``answer``; supersede an older unstarted task."""
        user = query.from_user
        user_id = user.id
        previous = self._slots.get(user_id)
        seq = 1 if previous is None else previous.seq + 1
        flying = self._in_flight.get(user_id)
        waiting = previous is not None and (user_id, previous.seq) in self._waiting
        if (
            previous is not None
            and not previous.task.done()
            and previous.task is not flying
            and not waiting
        ):
            previous.task.cancel()
            logger.info("inline_query_superseded")
        job = _Job(user_id=user_id, seq=seq, query=query, bot=bot, deps=deps, answer=answer)
        task = asyncio.create_task(self._run(job), name="telegram-inline")
        self._slots[user_id] = _Slot(seq=seq, task=task)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def is_current_task(self, user_id: int) -> bool:
        """True when the caller is the latest scheduled task for ``user_id``."""
        slot = self._slots.get(user_id)
        current = asyncio.current_task()
        return slot is not None and current is not None and slot.task is current

    def _still_latest(self, job: _Job) -> bool:
        slot = self._slots.get(job.user_id)
        return slot is not None and slot.seq == job.seq

    async def _run(self, job: _Job) -> None:
        try:
            await self._sleeper.sleep(self._debounce_seconds)
            marker = (job.user_id, job.seq)
            self._waiting.add(marker)
            try:
                flying = self._in_flight.get(job.user_id)
                slot = self._slots.get(job.user_id)
                if slot is not None and flying is not None and flying is not slot.task:
                    await flying
            finally:
                self._waiting.discard(marker)
            if not self._still_latest(job):
                return
            slot = self._slots[job.user_id]
            self._in_flight[job.user_id] = slot.task
            await job.answer(job.query, job.bot, job.deps)
        finally:
            running = asyncio.current_task()
            if self._in_flight.get(job.user_id) is running:
                del self._in_flight[job.user_id]
            current = self._slots.get(job.user_id)
            if current is not None and current.seq == job.seq:
                del self._slots[job.user_id]
