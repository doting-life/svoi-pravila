"""Valkey-backed global daily LLM token budget (ADR-0009)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from redis.asyncio import Redis

from svoi_pravila.adapters.cache._lua import load_lua
from svoi_pravila.adapters.cache._redis_map import map_redis
from svoi_pravila.adapters.cache.errors import CacheErrorKind, CacheUnavailable
from svoi_pravila.application.ports.billable_token_sum import BillableTokenSum
from svoi_pravila.application.ports.llm_budget import BudgetExhausted, BudgetOk
from svoi_pravila.domain.product_day import expire_at_utc, resets_at_utc


def _expire_unix(day: date, timezone: str) -> int:
    """Unix seconds for EXPIREAT at the product-day boundary."""
    return int(expire_at_utc(day, timezone).timestamp())


@dataclass(frozen=True, slots=True)
class ValkeyLlmBudgetConfig:
    """Static knobs for ``ValkeyLlmBudget``."""

    budget: int
    timezone: str
    key_prefix: str = "llm_budget"


class ValkeyLlmBudget:
    """Daily spent counter with SET NX rebuild from ``usage_events``.

    Residual race when the key is missing: concurrent rebuilders compute the
    same DB sum and one ``SET NX`` wins. An ``add`` whose event was committed
    after the winner's read but before its ``SET NX`` is counted by its own
    ``INCRBY``. An ``add`` whose event was included in the winner's read and
    that arrives after the SET can be counted twice. The bound is the number
    of in-flight adds during one rebuild; the error only over-counts
    (conservative).
    """

    def __init__(
        self,
        client: Redis,
        *,
        config: ValkeyLlmBudgetConfig,
        sums: BillableTokenSum,
    ) -> None:
        self._client = client
        self._budget = config.budget
        self._timezone = config.timezone
        self._sums = sums
        self._key_prefix = config.key_prefix
        self._add = client.register_script(load_lua("budget_add.lua"))

    def _key(self, day: date) -> str:
        return f"{self._key_prefix}:{day.isoformat()}"

    async def check(self, day: date) -> BudgetOk | BudgetExhausted:
        """Rebuild if missing; exhausted when spent >= budget."""
        spent = await self._ensure_key(day)
        resets = resets_at_utc(day, self._timezone).replace(microsecond=0)
        if spent >= self._budget:
            return BudgetExhausted(resets_at=resets)
        return BudgetOk()

    async def add(self, day: date, billable_tokens: int) -> None:
        """INCR when the key exists; otherwise rebuild from DB (no second INCR).

        Callers persist the usage event before ``add``. A missing key is rebuilt
        from ``usage_events``, which already includes that event, so a further
        ``INCRBY`` would double-count.
        """
        if billable_tokens < 0:
            msg = "billable_tokens must be non-negative"
            raise ValueError(msg)
        if billable_tokens == 0:
            await self._ensure_key(day)
            return
        key = self._key(day)

        async def _try_add() -> int:
            return int(await self._add(keys=[key], args=[billable_tokens]))

        added = await map_redis(_try_add)
        if added == 1:
            return
        await self._ensure_key(day)

    async def _ensure_key(self, day: date) -> int:
        key = self._key(day)

        async def _get() -> str | None:
            value = await self._client.get(key)
            if value is None:
                return None
            if isinstance(value, bytes):
                return value.decode()
            return str(value)

        existing = await map_redis(_get)
        if existing is not None:
            return int(existing)
        return await self._rebuild(day)

    async def _rebuild(self, day: date) -> int:
        key = self._key(day)
        expire_unix = _expire_unix(day, self._timezone)

        async def _get_again() -> str | None:
            value = await self._client.get(key)
            if value is None:
                return None
            if isinstance(value, bytes):
                return value.decode()
            return str(value)

        raced = await map_redis(_get_again)
        if raced is not None:
            return int(raced)
        total = await self._sums.sum_for_day(day)

        async def _set_nx() -> bool:
            return bool(
                await self._client.set(
                    key,
                    total,
                    nx=True,
                    exat=expire_unix,
                )
            )

        await map_redis(_set_nx)

        async def _read() -> str:
            value = await self._client.get(key)
            if value is None:
                raise CacheUnavailable(CacheErrorKind.SERVER)
            if isinstance(value, bytes):
                return value.decode()
            return str(value)

        return int(await map_redis(_read))
