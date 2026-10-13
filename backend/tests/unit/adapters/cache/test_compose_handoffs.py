"""Compose handoff Valkey adapter tests."""

from __future__ import annotations

from typing import cast
from uuid import UUID

import pytest
from redis.asyncio import Redis

from svoi_pravila.adapters.cache.compose_handoffs import ValkeyComposeHandoffs
from svoi_pravila.application.compose_handoff import (
    ComposeHandoffPayload,
    is_compose_handoff_ref,
)
from svoi_pravila.application.errors import ComposeHandoffUnavailable
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


def _store() -> tuple[ValkeyComposeHandoffs, _MemoryRedis]:
    memory = _MemoryRedis()
    return ValkeyComposeHandoffs(cast(Redis, memory), ttl_seconds=600), memory


@pytest.mark.unit
async def test_compose_handoff_roundtrip_single_use() -> None:
    store, memory = _store()
    user_id = UserId(UUID("01900000-0000-7000-8000-000000000010"))
    token = await store.store(
        ComposeHandoffPayload(user_id=user_id, draft="черновик"),
    )
    assert is_compose_handoff_ref(token)
    assert "черновик" not in "".join(memory.data.values())
    redeemed = await store.redeem_once(token)
    assert redeemed.draft == "черновик"
    assert redeemed.user_id == user_id
    with pytest.raises(ComposeHandoffUnavailable):
        await store.redeem_once(token)


@pytest.mark.unit
async def test_compose_handoff_tamper_rejected() -> None:
    store, _memory = _store()
    user_id = UserId(UUID("01900000-0000-7000-8000-000000000011"))
    token = await store.store(ComposeHandoffPayload(user_id=user_id, draft="x"))
    with pytest.raises(ComposeHandoffUnavailable):
        await store.redeem_once(token[:-1] + ("A" if token[-1] != "A" else "B"))
