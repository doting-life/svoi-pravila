"""Pseudonymizer port — deterministic HMAC-based identifiers."""

from __future__ import annotations

from typing import Protocol


class Pseudonymizer(Protocol):
    """Derive a hex pseudonym from a purpose label and input string."""

    def pseudonymize(self, purpose: str, value: str) -> str:
        """Return a hex digest deterministic for ``(purpose, value)``."""
        ...
