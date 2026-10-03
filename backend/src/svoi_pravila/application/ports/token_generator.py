"""Invite token generator port."""

from __future__ import annotations

from typing import Protocol


class TokenGenerator(Protocol):
    """Generates raw invite tokens."""

    def new_invite_token(self) -> str:
        """Return a URL-safe token with ≥256 bits entropy and ≤60 characters."""
        ...
