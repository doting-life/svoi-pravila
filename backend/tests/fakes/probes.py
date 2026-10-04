"""Fake readiness probes for unit tests."""

from __future__ import annotations

import asyncio

from svoi_pravila.application.ports.readiness import ProbeCheckResult


class OkProbe:
    """Probe that succeeds immediately."""

    def __init__(self, name: str = "ok") -> None:
        self.name = name

    async def check(self, timeout_seconds: float) -> ProbeCheckResult:
        del timeout_seconds
        return ProbeCheckResult(ready=True, reason="ok")


class FailingProbe:
    """Probe that reports not-ready without raising."""

    def __init__(self, name: str = "failing") -> None:
        self.name = name

    async def check(self, timeout_seconds: float) -> ProbeCheckResult:
        del timeout_seconds
        return ProbeCheckResult(ready=False, reason="failed")


class HangingProbe:
    """Probe that waits until the per-probe timeout, then reports timeout."""

    def __init__(self, name: str = "hanging") -> None:
        self.name = name

    async def check(self, timeout_seconds: float) -> ProbeCheckResult:
        try:
            await asyncio.wait_for(asyncio.sleep(3600), timeout=timeout_seconds)
        except TimeoutError:
            return ProbeCheckResult(ready=False, reason="timeout")
        return ProbeCheckResult(ready=True, reason="ok")


class RaisingProbe:
    """Probe that raises an unexpected error (must propagate)."""

    def __init__(self, name: str = "raising") -> None:
        self.name = name

    async def check(self, timeout_seconds: float) -> ProbeCheckResult:
        del timeout_seconds
        msg = "unexpected probe failure"
        raise RuntimeError(msg)
