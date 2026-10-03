"""Fake invite token generator."""

from __future__ import annotations


class FakeTokenGenerator:
    """Deterministic URL-safe tokens (≥256 bits when encoded)."""

    def __init__(self) -> None:
        self._counter = 0

    def new_invite_token(self) -> str:
        """Return the next token (43 chars of base64url ≈ 256 bits)."""
        self._counter += 1
        # 43 URL-safe characters from a deterministic alphabet; length ≤ 60.
        base = f"{self._counter:043d}"
        return base[:43]
