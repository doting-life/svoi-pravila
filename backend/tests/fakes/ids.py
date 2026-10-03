"""Fake id generator."""

from __future__ import annotations

import uuid


class FakeIdGenerator:
    """Deterministic sequential UUIDs."""

    def __init__(self) -> None:
        self._counter = 0

    def new_id(self) -> uuid.UUID:
        """Return the next sequential UUID."""
        self._counter += 1
        return uuid.UUID(int=self._counter)
