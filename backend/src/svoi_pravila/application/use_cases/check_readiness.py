"""Readiness check use case."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from svoi_pravila.application.ports.readiness import ReadinessProbe


class ProbeOutcome(StrEnum):
    """Per-probe readiness outcome (no exception detail)."""

    OK = "ok"
    FAILED = "failed"
    TIMEOUT = "timeout"


@dataclass(frozen=True, slots=True)
class ProbeStatus:
    """Status of a single readiness probe."""

    name: str
    status: ProbeOutcome


@dataclass(frozen=True, slots=True)
class ReadinessResult:
    """Aggregate readiness result."""

    ready: bool
    probes: tuple[ProbeStatus, ...]


class CheckReadiness:
    """Run readiness probes concurrently with a per-probe timeout."""

    def __init__(self, probes: Sequence[ReadinessProbe], timeout_seconds: float) -> None:
        self._probes = tuple(probes)
        self._timeout_seconds = timeout_seconds

    async def execute(self) -> ReadinessResult:
        """Return overall readiness and per-probe outcomes; never raise probe errors."""
        if not self._probes:
            return ReadinessResult(ready=True, probes=())

        statuses = await asyncio.gather(
            *(self._run_probe(probe) for probe in self._probes),
        )
        ready = all(status.status is ProbeOutcome.OK for status in statuses)
        return ReadinessResult(ready=ready, probes=tuple(statuses))

    async def _run_probe(self, probe: ReadinessProbe) -> ProbeStatus:
        try:
            await asyncio.wait_for(probe.check(), timeout=self._timeout_seconds)
        except TimeoutError:
            return ProbeStatus(name=probe.name, status=ProbeOutcome.TIMEOUT)
        except Exception:
            return ProbeStatus(name=probe.name, status=ProbeOutcome.FAILED)
        return ProbeStatus(name=probe.name, status=ProbeOutcome.OK)
