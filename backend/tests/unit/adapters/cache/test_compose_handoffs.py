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
from svoi_pravila.domain.ids import ContactId


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
    owner = "ab" * 32
    contact = ContactId(UUID("01900000-0000-7000-8000-000000000001"))
    token = await store.store(
        owner,
        ComposeHandoffPayload(draft="черновик", contact_id=contact),
    )
    assert is_compose_handoff_ref(token)
    assert "черновик" not in "".join(memory.data.values())
    redeemed = await store.redeem_once(owner, token)
    assert redeemed.draft == "черновик"
    assert redeemed.contact_id == contact
    with pytest.raises(ComposeHandoffUnavailable):
        await store.redeem_once(owner, token)


@pytest.mark.unit
async def test_compose_handoff_wrong_user_consumes_and_empty_contact() -> None:
    store, _memory = _store()
    owner = "ab" * 32
    token = await store.store(
        owner,
        ComposeHandoffPayload(draft="x", contact_id=None),
    )
    # GETDEL runs before AAD check, so a wrong-user redeem consumes the ciphertext.
    with pytest.raises(ComposeHandoffUnavailable):
        await store.redeem_once("cd" * 32, token)
    with pytest.raises(ComposeHandoffUnavailable):
        await store.redeem_once(owner, token)
    empty_contact = await store.store(
        owner,
        ComposeHandoffPayload(draft="solo", contact_id=None),
    )
    redeemed = await store.redeem_once(owner, empty_contact)
    assert redeemed.contact_id is None
    assert redeemed.draft == "solo"
