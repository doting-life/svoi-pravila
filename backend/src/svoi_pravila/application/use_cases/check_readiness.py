"""Readiness check use case."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from svoi_pravila.application.ports.readiness import ProbeCheckResult, ReadinessProbe


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
        """Return overall readiness and per-probe outcomes."""
        if not self._probes:
            return ReadinessResult(ready=True, probes=())

        statuses = await asyncio.gather(
            *(self._status(probe) for probe in self._probes),
        )
        ready = all(status.status is ProbeOutcome.OK for status in statuses)
        return ReadinessResult(ready=ready, probes=tuple(statuses))

    async def _status(self, probe: ReadinessProbe) -> ProbeStatus:
        result = await probe.check(self._timeout_seconds)
        return ProbeStatus(name=probe.name, status=_outcome(result))


def _outcome(result: ProbeCheckResult) -> ProbeOutcome:
    if result.ready:
        return ProbeOutcome.OK
    if result.reason == "timeout":
        return ProbeOutcome.TIMEOUT
    return ProbeOutcome.FAILED
