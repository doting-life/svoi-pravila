"""Unit tests for ValkeyExportDownloadStore edge paths."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

import pytest
from redis.asyncio import Redis

from svoi_pravila.adapters.cache.export_download import ValkeyExportDownloadStore
from svoi_pravila.domain.ids import UserId

_EXPIRES = datetime(2026, 1, 1, tzinfo=UTC)


class _MemoryRedis:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}
        self.force_set_false = False

    async def set(
        self,
        key: str,
        value: str,
        *,
        nx: bool = False,
        ex: int | None = None,
    ) -> bool | None:
        _ = ex
        if self.force_set_false:
            return False
        if nx and key in self.data:
            return False
        self.data[key] = value
        return True

    async def getdel(self, key: str) -> str | None:
        return self.data.pop(key, None)


@pytest.mark.unit
async def test_export_download_issue_collision() -> None:
    redis = _MemoryRedis()
    redis.force_set_false = True
    store = ValkeyExportDownloadStore(cast(Redis, redis))
    with pytest.raises(RuntimeError, match="collision"):
        await store.issue(UserId(UUID(int=1)), expires_at=_EXPIRES)


@pytest.mark.unit
async def test_export_download_consume_invalid_uuid() -> None:
    redis = _MemoryRedis()
    store = ValkeyExportDownloadStore(cast(Redis, redis))
    grant = await store.issue(UserId(UUID(int=2)), expires_at=_EXPIRES)
    key = next(iter(redis.data))
    redis.data[key] = "not-a-uuid"
    assert await store.consume(grant.raw_token) is None


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
