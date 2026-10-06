"""Bot username for invite deep-links (filled after getMe)."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class BotUsername(Protocol):
    """Mutable username holder; adapters implement this."""

    username: str | None
