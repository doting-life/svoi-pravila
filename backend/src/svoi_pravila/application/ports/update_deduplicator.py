"""Update deduplicator port for Telegram update_id."""

from __future__ import annotations

from typing import Protocol


class UpdateDeduplicator(Protocol):
    """Atomically claim a Telegram update_id for processing."""

    async def claim(self, update_id: int) -> bool:
        """Return True if this process should handle ``update_id`` (first claim)."""
        ...
