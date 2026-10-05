"""Mutable cache for the bot username from getMe (no new settings)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class BotUsernameCache:
    """Holds ``getMe().username`` after lifecycle start."""

    username: str | None = None
