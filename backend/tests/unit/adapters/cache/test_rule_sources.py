"""Rule-source sealed token store tests."""

from __future__ import annotations

import asyncio
from typing import cast
from uuid import UUID

import pytest
from redis.asyncio import Redis

from svoi_pravila.adapters.cache.rule_sources import ValkeyRuleSources
from svoi_pravila.adapters.cache.sealed_token import (
    SealedTokenCodec,
    encrypt_plaintext,
    seal_aad,
)
from svoi_pravila.application.errors import RuleSourceUnavailable
from svoi_pravila.application.rule_source import RULE_SOURCE_ID_LEN, RuleSourcePayload
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


def _store() -> tuple[ValkeyRuleSources, _MemoryRedis]:
    memory = _MemoryRedis()
    return ValkeyRuleSources(cast(Redis, memory), ttl_seconds=600), memory


@pytest.mark.unit
async def test_rule_source_token_is_opaque() -> None:
    store, _memory = _store()
    token = await store.store(
        "a" * 64,
        RuleSourcePayload(contact_id=ContactId(UUID(int=1)), incoming_text="привет"),
    )
    assert ":" not in token
    assert len(token) > 0


@pytest.mark.unit
async def test_rule_source_redeem_once_is_single_use() -> None:
    store, memory = _store()
    owner = "ab" * 32
    payload = RuleSourcePayload(contact_id=ContactId(UUID(int=2)), incoming_text="секрет")
    token = await store.store(owner, payload)
    assert "секрет" not in "".join(memory.data.values())
    redeemed = await store.redeem_once(owner, token)
    assert redeemed == payload
    assert memory.data == {}
    with pytest.raises(RuleSourceUnavailable):
        await store.redeem_once(owner, token)


@pytest.mark.unit
async def test_rule_source_wrong_user_rejected() -> None:
    store, _memory = _store()
    owner = "ab" * 32
    token = await store.store(
        owner,
        RuleSourcePayload(contact_id=ContactId(UUID(int=3)), incoming_text="x"),
    )
    with pytest.raises(RuleSourceUnavailable):
        await store.redeem_once("cd" * 32, token)


@pytest.mark.unit
async def test_rule_source_concurrent_redeem_once() -> None:
    store, _memory = _store()
    owner = "ab" * 32
    token = await store.store(
        owner,
        RuleSourcePayload(contact_id=ContactId(UUID(int=4)), incoming_text="once"),
    )

    async def _redeem() -> str:
        try:
            payload = await store.redeem_once(owner, token)
            return "ok:" + payload.incoming_text
        except RuleSourceUnavailable:
            return "miss"

    results = await asyncio.gather(_redeem(), _redeem())
    assert results.count("ok:once") == 1
    assert results.count("miss") == 1


@pytest.mark.unit
async def test_rule_source_corrupt_ciphertext_and_payload() -> None:
    store, memory = _store()
    owner = "ab" * 32
    token = await store.store(
        owner,
        RuleSourcePayload(contact_id=ContactId(UUID(int=5)), incoming_text="ok"),
    )
    key = next(iter(memory.data))
    memory.data[key] = "%%%"
    with pytest.raises(RuleSourceUnavailable):
        await store.redeem_once(owner, token)

    token2 = await store.store(
        owner,
        RuleSourcePayload(contact_id=ContactId(UUID(int=6)), incoming_text="ok"),
    )
    codec = SealedTokenCodec(id_len=RULE_SOURCE_ID_LEN, prefix="")
    token_id, aes_key = codec.parse(token2)
    redis_key = f"tg:rule_source:{token_id.hex()}"
    aad = seal_aad(aad_version="rule_source:v1", token_id=token_id, user_pseudonym=owner)
    memory.data[redis_key] = encrypt_plaintext(key=aes_key, plaintext=b"not-a-uuid\ntext", aad=aad)
    with pytest.raises(RuleSourceUnavailable):
        await store.redeem_once(owner, token2)


@pytest.mark.unit
async def test_rule_source_malformed_token() -> None:
    store, _memory = _store()
    with pytest.raises(RuleSourceUnavailable):
        await store.redeem_once("ab" * 32, "%%%")
