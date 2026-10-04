"""In-memory confirmation tokens for tests."""

from __future__ import annotations

from secrets import token_hex


class FakeConfirmationTokens:
    """Issue and consume tokens without Valkey."""

    def __init__(self) -> None:
        self._tokens: dict[tuple[str, str], str] = {}

    async def issue(self, *, pseudonym: str, action: str) -> str:
        key = (pseudonym, action)
        existing = self._tokens.get(key)
        if existing is not None:
            return existing
        token = token_hex(16)
        self._tokens[key] = token
        return token

    async def consume(self, *, pseudonym: str, action: str, token: str) -> bool:
        key = (pseudonym, action)
        if self._tokens.get(key) != token:
            return False
        del self._tokens[key]
        return True
