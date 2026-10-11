"""Unit tests for the shared sealed-token Valkey helpers."""

from __future__ import annotations

from typing import cast

import pytest
from redis.asyncio import Redis

from svoi_pravila.adapters.cache.sealed_token import (
    BearerBinding,
    SealedTokenCodec,
    SealedTokenUnavailableError,
    SealedValkeyStore,
    UserBinding,
    decrypt_stored,
    encrypt_plaintext,
    seal_aad,
)


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
def test_sealed_codec_rejects_short_id_and_bad_tokens() -> None:
    with pytest.raises(ValueError, match="at least 8"):
        SealedTokenCodec(id_len=7)
    codec = SealedTokenCodec(id_len=8, prefix="p_")
    _token_id, _key, token = codec.mint()
    assert token.startswith("p_")
    with pytest.raises(ValueError, match="prefix"):
        codec.parse("x_" + token[2:])
    with pytest.raises(ValueError, match="length"):
        SealedTokenCodec(id_len=8).parse("YWJj")


@pytest.mark.unit
def test_seal_aad_user_and_bearer_bindings() -> None:
    token_id = b"abcdefgh"
    user = UserBinding(pseudonym="pseudo")
    bearer = BearerBinding()
    assert seal_aad(aad_version="p:v1", token_id=token_id, binding=user) == (
        b"p:v1:" + token_id.hex().encode() + b":pseudo"
    )
    assert seal_aad(aad_version="p:v1", token_id=token_id, binding=bearer) == (
        b"p:v1:" + token_id.hex().encode()
    )


@pytest.mark.unit
async def test_sealed_store_user_and_bearer_bindings() -> None:
    memory = _MemoryRedis()
    codec = SealedTokenCodec(id_len=8)
    store = SealedValkeyStore(
        cast(Redis, memory),
        ttl_seconds=60,
        key_prefix="tg:seal",
        aad_version="test:v1",
        codec=codec,
    )
    user = UserBinding(pseudonym="owner")
    other = UserBinding(pseudonym="other")
    bearer = BearerBinding()

    token = await store.store(user, b"secret")
    assert await store.get(user, token) == b"secret"
    with pytest.raises(SealedTokenUnavailableError):
        await store.get(user, "not-a-token")
    with pytest.raises(SealedTokenUnavailableError):
        await store.get(other, token)
    with pytest.raises(SealedTokenUnavailableError):
        await store.get(bearer, token)

    bearer_token = await store.store(bearer, b"bearer-secret")
    assert await store.get(bearer, bearer_token) == b"bearer-secret"
    with pytest.raises(SealedTokenUnavailableError):
        await store.get(user, bearer_token)

    memory.data[next(iter(memory.data))] = "%%%"
    with pytest.raises(SealedTokenUnavailableError):
        await store.get(user, token)

    token2 = await store.store(user, b"once")
    assert await store.getdel(user, token2) == b"once"
    with pytest.raises(SealedTokenUnavailableError):
        await store.getdel(user, token2)
    with pytest.raises(SealedTokenUnavailableError):
        await store.getdel(user, "bad")

    token3 = await store.store(user, b"del")
    await store.delete(token3)
    with pytest.raises(SealedTokenUnavailableError):
        await store.delete("bad-token")

    key = b"k" * 32
    aad = seal_aad(
        aad_version="test:v1",
        token_id=b"abcdefgh",
        binding=UserBinding(pseudonym="u"),
    )
    stored = encrypt_plaintext(key=key, plaintext=b"x", aad=aad)
    assert decrypt_stored(key=key, stored=stored, aad=aad) == b"x"
