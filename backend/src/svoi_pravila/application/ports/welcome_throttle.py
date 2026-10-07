"""Throttle private-chat welcome replies (C0)."""

from __future__ import annotations

from typing import Protocol

WELCOME_THROTTLE_PURPOSE = "dm_welcome"
WELCOME_THROTTLE_TTL_SECONDS = 600


class WelcomeThrottle(Protocol):
    """Claim a one-shot welcome window per HMAC pseudonym."""

    async def claim(self, pseudonym: str) -> bool:
        """Return True when this claim wins the TTL window (SET NX EX)."""
        ...
