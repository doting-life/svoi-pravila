"""Prepared-result token store: ciphertext-only Valkey adapter."""

from __future__ import annotations

from typing import cast

import pytest
from redis.asyncio import Redis

from svoi_pravila.adapters.cache.prepared_results import (
    ValkeyPreparedResults,
    _parse_plaintext,
)
from svoi_pravila.application.errors import PreparedResultUnavailable
from svoi_pravila.application.ports.prepared_results import PreparedVariant
from svoi_pravila.application.prepared_token import (
    INLINE_QUERY_LIMIT,
    TOKEN_LENGTH,
    is_prepared_token,
)
from svoi_pravila.domain.enums import Firmness


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


def _store() -> tuple[ValkeyPreparedResults, _MemoryRedis]:
    memory = _MemoryRedis()
    return ValkeyPreparedResults(cast(Redis, memory), ttl_seconds=600), memory


@pytest.mark.unit
async def test_prepared_token_length_fits_inline_query_limit() -> None:
    store, _memory = _store()
    token = await store.store("a" * 64, PreparedVariant(Firmness.GENTLE, "hello"))
    assert len(token) == TOKEN_LENGTH
    assert TOKEN_LENGTH < INLINE_QUERY_LIMIT
    assert is_prepared_token(token)


@pytest.mark.unit
async def test_prepared_redeem_roundtrip_and_delete() -> None:
    store, memory = _store()
    owner = "ab" * 32
    token = await store.store(owner, PreparedVariant(Firmness.FIRM, "keep this"))
    redeemed = await store.redeem(owner, token)
    assert redeemed.text == "keep this"
    assert redeemed.firmness is Firmness.FIRM
    assert "keep this" not in "".join(memory.data.values())
    await store.delete(owner, token)
    with pytest.raises(PreparedResultUnavailable):
        await store.redeem(owner, token)


@pytest.mark.unit
async def test_prepared_wrong_user_and_tamper_rejected() -> None:
    store, memory = _store()
    owner = "ab" * 32
    token = await store.store(owner, PreparedVariant(Firmness.BALANCED, "secret-text"))
    with pytest.raises(PreparedResultUnavailable):
        await store.redeem("cd" * 32, token)
    key = next(iter(memory.data))
    blob = memory.data[key]
    flipped = ("A" if blob[0] != "A" else "B") + blob[1:]
    memory.data[key] = flipped
    with pytest.raises(PreparedResultUnavailable):
        await store.redeem(owner, token)
    with pytest.raises(PreparedResultUnavailable):
        await store.redeem(owner, "not-a-token")
    with pytest.raises(PreparedResultUnavailable):
        await store.redeem(owner, "p_" + "!" * 64)


@pytest.mark.unit
async def test_prepared_missing_and_corrupt_value() -> None:
    store, memory = _store()
    owner = "ab" * 32
    token = await store.store(owner, PreparedVariant(Firmness.GENTLE, "x"))
    memory.data.clear()
    with pytest.raises(PreparedResultUnavailable):
        await store.redeem(owner, token)
    token = await store.store(owner, PreparedVariant(Firmness.GENTLE, "x"))
    memory.data[next(iter(memory.data))] = "%%%"
    with pytest.raises(PreparedResultUnavailable):
        await store.redeem(owner, token)


@pytest.mark.unit
def test_prepared_plaintext_and_token_decode_failures() -> None:
    with pytest.raises(PreparedResultUnavailable):
        _parse_plaintext(b"gentle-no-newline")
    with pytest.raises(PreparedResultUnavailable):
        _parse_plaintext(b"nope\ntext")
    with pytest.raises(PreparedResultUnavailable):
        _parse_plaintext(b"\xff")
