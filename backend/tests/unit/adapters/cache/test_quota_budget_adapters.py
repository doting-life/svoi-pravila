"""Unit coverage for quota gate, LLM budget, Lua loader, and redis error map."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, cast

import pytest
from redis.asyncio import Redis
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import RedisError
from redis.exceptions import TimeoutError as RedisTimeoutError

from svoi_pravila.adapters.cache import errors as cache_errors
from svoi_pravila.adapters.cache._lua import load_lua
from svoi_pravila.adapters.cache._redis_map import map_redis
from svoi_pravila.adapters.cache.llm_budget import ValkeyLlmBudget, ValkeyLlmBudgetConfig
from svoi_pravila.adapters.cache.quota_gate import ValkeyQuotaGate
from svoi_pravila.application.errors import CacheErrorKind, CacheUnavailable
from svoi_pravila.application.ports.llm_budget import BudgetExhausted, BudgetOk
from svoi_pravila.application.ports.quota_gate import QuotaExhausted, Reserved
from svoi_pravila.domain.enums import QuotaClass
from svoi_pravila.domain.product_day import product_day

_TZ = "Europe/Moscow"
_DAY = product_day(datetime.now(UTC), _TZ)
_PSEUDO = "ab" * 32
_KEY = f"llm_budget:{_DAY.isoformat()}"


@pytest.mark.unit
def test_cache_errors_reexport() -> None:
    assert cache_errors.CacheErrorKind is CacheErrorKind
    assert cache_errors.CacheUnavailable is CacheUnavailable


@pytest.mark.unit
def test_load_lua_scripts() -> None:
    for name in ("quota_reserve.lua", "quota_refund.lua", "budget_add.lua"):
        body = load_lua(name)
        assert "redis.call" in body
    reserve = load_lua("quota_reserve.lua")
    assert "ARGV[3]" not in reserve
    assert "n > limit" not in reserve


@pytest.mark.unit
async def test_map_redis_translates_failures() -> None:
    async def ok() -> int:
        return 7

    assert await map_redis(ok) == 7

    async def timeout() -> None:
        raise RedisTimeoutError("t")

    with pytest.raises(CacheUnavailable) as timed:
        await map_redis(timeout)
    assert timed.value.kind is CacheErrorKind.TIMEOUT

    async def network() -> None:
        raise RedisConnectionError("n")

    with pytest.raises(CacheUnavailable) as net:
        await map_redis(network)
    assert net.value.kind is CacheErrorKind.NETWORK

    async def server() -> None:
        raise RedisError("s")

    with pytest.raises(CacheUnavailable) as srv:
        await map_redis(server)
    assert srv.value.kind is CacheErrorKind.SERVER

    async def os_err() -> None:
        raise OSError("o")

    with pytest.raises(CacheUnavailable) as ose:
        await map_redis(os_err)
    assert ose.value.kind is CacheErrorKind.SERVER


class _ScriptClient:
    """Minimal Redis stand-in for ValkeyQuotaGate scripts."""

    def __init__(self) -> None:
        self.counters: dict[str, int] = {}
        self.markers: dict[str, str] = {}

    def register_script(self, lua: str) -> Any:
        if "EXPIREAT" in lua and "INCR" in lua:
            return self._reserve_script
        return self._refund_script

    async def _reserve_script(self, *, keys: list[str], args: list[Any]) -> list[int]:
        counter, marker = keys
        limit = int(args[0])
        expire_at = int(args[1])
        assert len(args) == 2
        current = self.counters.get(counter, 0)
        if current >= limit:
            return [0, current, expire_at]
        n = current + 1
        self.counters[counter] = n
        self.markers[marker] = "1"
        return [1, limit - n, expire_at]

    async def _refund_script(self, *, keys: list[str], args: list[Any]) -> int:
        del args
        counter, marker = keys
        if self.markers.get(marker) != "1":
            return 0
        self.markers[marker] = "0"
        n = self.counters.get(counter, 0)
        if n > 0:
            self.counters[counter] = n - 1
        return 1


@pytest.mark.unit
async def test_quota_gate_reserve_refund_and_exhaust() -> None:
    client = _ScriptClient()
    gate = ValkeyQuotaGate(
        cast(Redis, client),
        inline_limit=2,
        decode_limit=1,
        timezone=_TZ,
    )
    first = await gate.reserve(_PSEUDO, QuotaClass.DECODE, _DAY)
    assert isinstance(first, Reserved)
    assert first.remaining == 0
    blocked = await gate.reserve(_PSEUDO, QuotaClass.DECODE, _DAY)
    assert isinstance(blocked, QuotaExhausted)
    await gate.refund(first.reservation)
    await gate.refund(first.reservation)
    again = await gate.reserve(_PSEUDO, QuotaClass.DECODE, _DAY)
    assert isinstance(again, Reserved)


class _BudgetClient:
    """In-memory Redis for ValkeyLlmBudget paths."""

    def __init__(self) -> None:
        self.data: dict[str, str | bytes] = {}
        self.get_misses = 0
        self.plant_on_second_get = False

    def register_script(self, lua: str) -> Any:
        del lua

        async def _add(*, keys: list[str], args: list[Any]) -> list[int]:
            day_key, hour_key = keys
            if day_key not in self.data:
                return [0, 0, 0]
            delta = int(args[0])
            threshold = int(args[2])
            current = self.data[day_key]
            as_str = current.decode() if isinstance(current, bytes) else current
            self.data[day_key] = str(int(as_str) + delta)
            prev_raw = self.data.get(hour_key)
            prev = int(prev_raw.decode() if isinstance(prev_raw, bytes) else prev_raw or 0)
            hour_tokens = prev + delta
            self.data[hour_key] = str(hour_tokens)
            crossed = 1 if prev < threshold <= hour_tokens else 0
            return [1, crossed, hour_tokens]

        return _add

    async def get(self, key: str) -> str | bytes | None:
        if key not in self.data:
            self.get_misses += 1
            if self.plant_on_second_get and self.get_misses >= 2:
                self.data[key] = b"55"
                return b"55"
            return None
        return self.data[key]

    async def set(
        self,
        key: str,
        value: str | int,
        *,
        nx: bool = False,
        ex: int | None = None,
        exat: int | None = None,
    ) -> bool:
        del ex, exat
        if nx and key in self.data:
            return False
        self.data[key] = str(value)
        return True


class _CountingSum:
    def __init__(self, total: int = 10) -> None:
        self.total = total
        self.calls = 0

    async def sum_for_day(self, day: object) -> int:
        del day
        self.calls += 1
        return self.total


def _make_budget(
    client: _BudgetClient,
    sums: _CountingSum,
    *,
    budget: int = 10,
) -> ValkeyLlmBudget:
    from tests.fakes.clock import FakeClock

    return ValkeyLlmBudget(
        cast(Redis, client),
        config=ValkeyLlmBudgetConfig(budget=budget, timezone=_TZ),
        sums=sums,
        clock=FakeClock(),
    )


@pytest.mark.unit
async def test_llm_budget_check_add_rebuild_no_double_count() -> None:
    client = _BudgetClient()
    sums = _CountingSum(total=5)
    budget = _make_budget(client, sums)

    assert await budget.check(_DAY) == BudgetOk()
    assert sums.calls == 1
    assert client.data[_KEY] == "5"

    await budget.add(_DAY, 0)
    assert sums.calls == 1
    await budget.add(_DAY, 3)
    assert client.data[_KEY] == "8"

    with pytest.raises(ValueError, match="non-negative"):
        await budget.add(_DAY, -1)

    client.data[_KEY] = b"12"
    exhausted = await budget.check(_DAY)
    assert isinstance(exhausted, BudgetExhausted)

    del client.data[_KEY]
    # DB sum already includes this event's tokens; rebuild must not INCR again.
    sums.total = 42
    await budget.add(_DAY, 7)
    assert client.data[_KEY] == "42"
    assert sums.calls == 2


@pytest.mark.unit
async def test_llm_budget_rebuild_raced_key() -> None:
    class _RaceStrClient(_BudgetClient):
        async def get(self, key: str) -> str | bytes | None:
            if key not in self.data:
                self.get_misses += 1
                if self.get_misses >= 2:
                    self.data[key] = "55"
                    return "55"
                return None
            return self.data[key]

    client = _RaceStrClient()
    sums = _CountingSum(total=9)
    budget = _make_budget(client, sums, budget=100)
    assert await budget.check(_DAY) == BudgetOk()
    assert client.data[_KEY] == "55"
    assert sums.calls == 0

    client_bytes = _BudgetClient()
    client_bytes.plant_on_second_get = True
    assert await _make_budget(client_bytes, _CountingSum(), budget=100).check(_DAY) == BudgetOk()
    assert int(client_bytes.data[_KEY]) == 55


@pytest.mark.unit
async def test_llm_budget_read_str_and_bytes_after_set() -> None:
    class _BytesAfterSet(_BudgetClient):
        async def set(
            self,
            key: str,
            value: str | int,
            *,
            nx: bool = False,
            ex: int | None = None,
            exat: int | None = None,
        ) -> bool:
            del ex, exat
            if nx and key in self.data:
                return False
            self.data[key] = str(value).encode()
            return True

    str_client = _BudgetClient()
    assert (
        await _make_budget(str_client, _CountingSum(total=3), budget=100).check(_DAY) == BudgetOk()
    )
    assert str_client.data[_KEY] == "3"

    bytes_client = _BytesAfterSet()
    assert (
        await _make_budget(bytes_client, _CountingSum(total=7), budget=100).check(_DAY)
        == BudgetOk()
    )
    assert int(bytes_client.data[_KEY]) == 7


@pytest.mark.unit
async def test_llm_budget_set_nx_lose_then_read() -> None:
    class _NxLoseClient(_BudgetClient):
        async def set(
            self,
            key: str,
            value: str | int,
            *,
            nx: bool = False,
            ex: int | None = None,
            exat: int | None = None,
        ) -> bool:
            del ex, exat
            if nx and key.startswith("llm_budget:"):
                self.data[key] = "88"
                return False
            if nx:
                self.data[key] = str(value)
                return True
            self.data[key] = str(value)
            return True

    client = _NxLoseClient()
    sums = _CountingSum(total=1)
    budget = _make_budget(client, sums, budget=100)
    assert await budget.check(_DAY) == BudgetOk()
    assert client.data[_KEY] == "88"
    assert sums.calls == 1


@pytest.mark.unit
async def test_llm_budget_read_after_set_requires_key() -> None:
    class _VanishClient(_BudgetClient):
        async def set(
            self,
            key: str,
            value: str | int,
            *,
            nx: bool = False,
            ex: int | None = None,
            exat: int | None = None,
        ) -> bool:
            del value, ex, exat
            if nx and key.startswith("llm_budget:"):
                return True
            return True

        async def get(self, key: str) -> str | bytes | None:
            del key
            return None

    client = _VanishClient()
    budget = _make_budget(client, _CountingSum(total=3), budget=100)
    with pytest.raises(CacheUnavailable) as missing:
        await budget.check(_DAY)
    assert missing.value.kind is CacheErrorKind.SERVER


@pytest.mark.unit
async def test_llm_budget_add_logs_and_raises_on_valkey_failure(
    capture_log_events: Callable[[], list[dict[str, Any]]],
) -> None:
    class _BoomClient(_BudgetClient):
        def register_script(self, lua: str) -> Any:
            del lua

            async def _add(*, keys: list[str], args: list[Any]) -> list[int]:
                del keys, args
                raise RedisConnectionError("down")

            return _add

    client = _BoomClient()
    client.data[_KEY] = "0"
    budget = _make_budget(client, _CountingSum())
    with pytest.raises(CacheUnavailable) as caught:
        await budget.add(_DAY, 10)
    assert caught.value.kind is CacheErrorKind.NETWORK
    failures = [e for e in capture_log_events() if e.get("event") == "llm_budget_add_failed"]
    assert len(failures) == 1
    assert failures[0]["error_kind"] == CacheErrorKind.NETWORK.value
