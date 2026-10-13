"""Unit tests for Valkey compose choice tokens."""

from __future__ import annotations

import asyncio
import base64
from typing import cast
from uuid import UUID

import pytest
from hypothesis import given
from hypothesis import strategies as st
from redis.asyncio import Redis
from tests.fakes.rate_limit import FakePseudonymizer

from svoi_pravila.adapters.cache.compose_choice_tokens import ValkeyComposeChoiceTokens
from svoi_pravila.application.errors import ComposeChoiceTokenInvalid
from svoi_pravila.application.ports.compose_choice_tokens import (
    COMPOSE_CHOICE_TTL_SECONDS,
    ComposeChoiceBinding,
)
from svoi_pravila.domain.enums import UsageScenario
from svoi_pravila.domain.ids import ContactId

_CONTACT = ContactId(UUID(int=7))


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


class _BytesGetRedis:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        _ = ex
        self.data[key] = value

    async def get(self, key: str) -> bytes | None:
        value = self.data.get(key)
        return None if value is None else value.encode("ascii")

    async def delete(self, *keys: str) -> int:
        removed = 0
        for key in keys:
            if key in self.data:
                del self.data[key]
                removed += 1
        return removed


class _DeleteZeroRedis(_MemoryRedis):
    async def delete(self, *keys: str) -> int:
        return 0


def _tokens(
    memory: _MemoryRedis | None = None,
    *,
    ttl_seconds: int = COMPOSE_CHOICE_TTL_SECONDS,
) -> tuple[ValkeyComposeChoiceTokens, _MemoryRedis]:
    store = memory or _MemoryRedis()
    return (
        ValkeyComposeChoiceTokens(
            cast(Redis, store),
            FakePseudonymizer(),
            ttl_seconds=ttl_seconds,
        ),
        store,
    )


@pytest.mark.unit
def test_ttl_bounds() -> None:
    with pytest.raises(ValueError, match="ttl_seconds"):
        ValkeyComposeChoiceTokens(cast(Redis, _MemoryRedis()), FakePseudonymizer(), ttl_seconds=0)
    with pytest.raises(ValueError, match="ttl_seconds"):
        ValkeyComposeChoiceTokens(
            cast(Redis, _MemoryRedis()),
            FakePseudonymizer(),
            ttl_seconds=COMPOSE_CHOICE_TTL_SECONDS + 1,
        )


@pytest.mark.unit
async def test_issue_and_redeem_once() -> None:
    tokens, store = _tokens()
    token = await tokens.issue("user-a", UsageScenario.SOFTEN, None)
    assert token.startswith("c_")
    assert store.data
    binding = await tokens.redeem_once("user-a", token)
    assert binding == ComposeChoiceBinding(UsageScenario.SOFTEN, None)
    assert store.data == {}
    with pytest.raises(ComposeChoiceTokenInvalid):
        await tokens.redeem_once("user-a", token)


@pytest.mark.unit
async def test_redeem_rejects_wrong_user_and_corrupt_tokens() -> None:
    tokens, store = _tokens()
    token = await tokens.issue("user-a", UsageScenario.HELP_SAY, _CONTACT)
    with pytest.raises(ComposeChoiceTokenInvalid):
        await tokens.redeem_once("user-b", token)
    assert store.data
    with pytest.raises(ComposeChoiceTokenInvalid):
        await tokens.redeem_once("user-a", "not-a-token")
    with pytest.raises(ComposeChoiceTokenInvalid):
        await tokens.redeem_once("user-a", "c_%%%")
    short = "c_" + base64.urlsafe_b64encode(b"short").decode("ascii").rstrip("=")
    with pytest.raises(ComposeChoiceTokenInvalid):
        await tokens.redeem_once("user-a", short)


@pytest.mark.unit
async def test_redeem_rejects_corrupt_stored_scenario() -> None:
    tokens, store = _tokens()
    token = await tokens.issue("user-a", UsageScenario.SOFTEN, None)
    key = next(iter(store.data))
    for corrupt in ("not-a-scenario|", "soften", "soften|not-a-uuid", "|"):
        store.data[key] = corrupt
        with pytest.raises(ComposeChoiceTokenInvalid):
            await tokens.redeem_once("user-a", token)


@pytest.mark.unit
async def test_redeem_rejects_tampered_stored_contact() -> None:
    tokens, store = _tokens()
    token = await tokens.issue("user-a", UsageScenario.SOFTEN, _CONTACT)
    key = next(iter(store.data))
    for swapped in (f"soften|{UUID(int=99)}", "soften|", f"decode|{_CONTACT}"):
        store.data[key] = swapped
        with pytest.raises(ComposeChoiceTokenInvalid):
            await tokens.redeem_once("user-a", token)
    assert store.data


@pytest.mark.unit
async def test_redeem_returns_bound_contact() -> None:
    tokens, _store = _tokens()
    token = await tokens.issue("user-a", UsageScenario.SOFTEN, _CONTACT)
    binding = await tokens.redeem_once("user-a", token)
    assert binding == ComposeChoiceBinding(UsageScenario.SOFTEN, _CONTACT)


@pytest.mark.unit
@given(
    scenario=st.sampled_from(list(UsageScenario)),
    contact=st.none() | st.uuids().map(ContactId),
    user=st.text(min_size=1, max_size=40),
)
def test_binding_round_trip_property(
    scenario: UsageScenario, contact: ContactId | None, user: str
) -> None:
    async def run() -> ComposeChoiceBinding:
        tokens, _store = _tokens()
        token = await tokens.issue(user, scenario, contact)
        return await tokens.redeem_once(user, token)

    assert asyncio.run(run()) == ComposeChoiceBinding(scenario, contact)


@pytest.mark.unit
async def test_redeem_rejects_missing_key_and_delete_race() -> None:
    tokens, store = _tokens()
    token = await tokens.issue("user-a", UsageScenario.DECODE, None)
    store.data.clear()
    with pytest.raises(ComposeChoiceTokenInvalid):
        await tokens.redeem_once("user-a", token)

    race_store = _DeleteZeroRedis()
    race_tokens = ValkeyComposeChoiceTokens(cast(Redis, race_store), FakePseudonymizer())
    token2 = await race_tokens.issue("user-a", UsageScenario.SOFTEN, None)
    with pytest.raises(ComposeChoiceTokenInvalid):
        await race_tokens.redeem_once("user-a", token2)


@pytest.mark.unit
async def test_redeem_accepts_bytes_stored_value() -> None:
    memory = _BytesGetRedis()
    tokens = ValkeyComposeChoiceTokens(cast(Redis, memory), FakePseudonymizer())
    token = await tokens.issue("user-a", UsageScenario.HELP_SAY, _CONTACT)
    binding = await tokens.redeem_once("user-a", token)
    assert binding == ComposeChoiceBinding(UsageScenario.HELP_SAY, _CONTACT)
