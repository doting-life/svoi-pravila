"""Unit coverage for confirmation-token SET NX races."""

from __future__ import annotations

from typing import cast

import pytest
from redis.asyncio import Redis

from svoi_pravila.adapters.cache.confirmation_tokens import ValkeyConfirmationTokens


class _RaceClient:
    def __init__(self) -> None:
        self._sets = 0

    def register_script(self, _lua: str) -> object:
        async def _run(*, keys: list[str], args: list[str]) -> int:
            return 0

        return _run

    async def set(self, _key: str, _value: str, *, nx: bool = False, ex: int | None = None) -> bool:
        self._sets += 1
        return self._sets > 1

    async def get(self, _key: str) -> str | None:
        return None


@pytest.mark.unit
async def test_issue_rewrites_when_nx_loses_and_key_vanishes() -> None:
    tokens = ValkeyConfirmationTokens(cast(Redis, _RaceClient()))
    token = await tokens.issue(pseudonym="p", action="rv")
    assert len(token) == 32
    assert await tokens.consume(pseudonym="p", action="rv", token=token) is False
