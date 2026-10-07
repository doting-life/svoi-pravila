"""Unit tests for ValkeyExportDownloadStore edge paths."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

import pytest
from redis.asyncio import Redis
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError

from svoi_pravila.adapters.cache.export_download import ValkeyExportDownloadStore
from svoi_pravila.application.errors import CacheErrorKind, CacheUnavailable
from svoi_pravila.domain.ids import UserId

_EXPIRES = datetime(2026, 1, 1, tzinfo=UTC)


class _MemoryRedis:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}
        self.force_set_false = False
        self.raise_on: Exception | None = None

    async def set(
        self,
        key: str,
        value: str,
        *,
        nx: bool = False,
        ex: int | None = None,
    ) -> bool | None:
        _ = ex
        if self.raise_on is not None:
            raise self.raise_on
        if self.force_set_false:
            return False
        if nx and key in self.data:
            return False
        self.data[key] = value
        return True

    async def getdel(self, key: str) -> str | None:
        if self.raise_on is not None:
            raise self.raise_on
        return self.data.pop(key, None)


@pytest.mark.unit
async def test_export_download_issue_collision() -> None:
    redis = _MemoryRedis()
    redis.force_set_false = True
    store = ValkeyExportDownloadStore(cast(Redis, redis))
    with pytest.raises(CacheUnavailable) as caught:
        await store.issue(UserId(UUID(int=1)), expires_at=_EXPIRES)
    assert caught.value.kind is CacheErrorKind.SERVER


@pytest.mark.unit
async def test_export_download_issue_unexpected_set_truthy() -> None:
    """Non-bool SET replies are treated as success (redis-py quirk path)."""

    class _OddSet(_MemoryRedis):
        async def set(
            self,
            key: str,
            value: str,
            *,
            nx: bool = False,
            ex: int | None = None,
        ) -> bool | None:
            del key, value, nx, ex
            return cast(bool | None, "OK")

    store = ValkeyExportDownloadStore(cast(Redis, _OddSet()))
    grant = await store.issue(UserId(UUID(int=5)), expires_at=_EXPIRES)
    assert grant.raw_token


@pytest.mark.unit
async def test_export_download_consume_invalid_uuid() -> None:
    redis = _MemoryRedis()
    store = ValkeyExportDownloadStore(cast(Redis, redis))
    grant = await store.issue(UserId(UUID(int=2)), expires_at=_EXPIRES)
    key = next(iter(redis.data))
    redis.data[key] = "not-a-uuid"
    with pytest.raises(CacheUnavailable) as caught:
        await store.consume(grant.raw_token)
    assert caught.value.kind is CacheErrorKind.SERVER


@pytest.mark.unit
async def test_export_download_consume_bytes_value() -> None:
    class _BytesRedis(_MemoryRedis):
        async def getdel(self, key: str) -> Any:
            raw = self.data.pop(key, None)
            return None if raw is None else raw.encode("utf-8")

    redis = _BytesRedis()
    store = ValkeyExportDownloadStore(cast(Redis, redis))
    grant = await store.issue(UserId(UUID(int=3)), expires_at=_EXPIRES)
    assert await store.consume(grant.raw_token) == UserId(UUID(int=3))


@pytest.mark.unit
async def test_export_download_maps_redis_timeout() -> None:
    redis = _MemoryRedis()
    redis.raise_on = RedisTimeoutError("timeout")
    store = ValkeyExportDownloadStore(cast(Redis, redis))
    with pytest.raises(CacheUnavailable) as caught:
        await store.issue(UserId(UUID(int=4)), expires_at=_EXPIRES)
    assert caught.value.kind is CacheErrorKind.TIMEOUT


@pytest.mark.unit
async def test_export_download_maps_redis_connection() -> None:
    redis = _MemoryRedis()
    redis.raise_on = RedisConnectionError("down")
    store = ValkeyExportDownloadStore(cast(Redis, redis))
    with pytest.raises(CacheUnavailable) as caught:
        await store.consume("any-token")
    assert caught.value.kind is CacheErrorKind.NETWORK
