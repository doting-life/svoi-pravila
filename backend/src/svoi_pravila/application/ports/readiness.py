"""Readiness probe port."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class ProbeCheckResult:
    """Typed probe outcome. ``reason`` is a C0 token (never exception text)."""

    ready: bool
    reason: str


class ReadinessProbe(Protocol):
    """Dependency that can be checked for readiness."""

    name: str

    async def check(self, timeout_seconds: float) -> ProbeCheckResult:
        """Return a typed result for expected failures; unexpected errors propagate."""
        ...
