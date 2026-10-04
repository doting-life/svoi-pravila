"""Single-use confirmation nonces for destructive Telegram actions."""

from __future__ import annotations

from typing import Protocol


class ConfirmationTokens(Protocol):
    """Issue and consume short-lived confirmation tokens per user action."""

    async def issue(self, *, pseudonym: str, action: str) -> str:
        """Create or reuse a 128-bit hex nonce for ``(pseudonym, action)``."""
        ...

    async def consume(self, *, pseudonym: str, action: str, token: str) -> bool:
        """Atomically delete the nonce if it matches; False on miss or replay."""
        ...
