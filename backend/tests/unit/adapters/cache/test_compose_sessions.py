"""Compose session Valkey adapter tests."""

from __future__ import annotations

from typing import cast
from uuid import UUID

import pytest
from redis.asyncio import Redis

from svoi_pravila.adapters.cache.compose_sessions import ValkeyComposeSessions
from svoi_pravila.application.compose_session import (
    ComposeSessionPayload,
    is_compose_session_ref,
)
from svoi_pravila.application.errors import ComposeSessionUnavailable
from svoi_pravila.domain.ids import UserId


class _MemoryRedis:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        _ = ex
        self.data[key] = value

    async def get(self, key: str) -> str | None:
        return self.data.get(key)

    async def delete(self, *keys: str) -> int:
        removed = 0
        for key in keys:
            if key in self.data:
                del self.data[key]
                removed += 1
        return removed

    async def getdel(self, key: str) -> str | None:
        return self.data.pop(key, None)


@pytest.mark.unit
async def test_compose_session_reusable_get() -> None:
    memory = _MemoryRedis()
    store = ValkeyComposeSessions(cast(Redis, memory), ttl_seconds=900)
    user_id = UserId(UUID("01900000-0000-7000-8000-000000000020"))
    token = await store.store(ComposeSessionPayload(user_id=user_id))
    assert is_compose_session_ref(token)
    first = await store.load(token)
    second = await store.load(token)
    assert first == second == ComposeSessionPayload(user_id=user_id)
    with pytest.raises(ComposeSessionUnavailable):
        await store.load("cs_" + ("a" * 64))
