"""Fake readiness probes for unit tests."""

from __future__ import annotations

import asyncio


class OkProbe:
    """Probe that succeeds immediately."""

    def __init__(self, name: str = "ok") -> None:
        self.name = name

    async def check(self) -> None:
        return None


class FailingProbe:
    """Probe that raises on check."""

    def __init__(self, name: str = "failing") -> None:
        self.name = name

    async def check(self) -> None:
        msg = "probe failed"
        raise RuntimeError(msg)


class HangingProbe:
    """Probe that hangs longer than any reasonable timeout."""

    def __init__(self, name: str = "hanging") -> None:
        self.name = name

    async def check(self) -> None:
        await asyncio.sleep(3600)
