"""Application ports (protocols)."""

from __future__ import annotations

from typing import Protocol


class ReadinessProbe(Protocol):
    """Dependency that can be checked for readiness."""

    name: str

    async def check(self) -> None:
        """Raise on failure; return normally when the dependency is ready."""
        ...
