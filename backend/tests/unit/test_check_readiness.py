"""CheckReadiness use case tests."""

from __future__ import annotations

import pytest

from svoi_pravila.application.check_readiness import CheckReadiness, ProbeOutcome
from tests.fakes.probes import FailingProbe, HangingProbe, OkProbe


@pytest.mark.unit
async def test_all_ok() -> None:
    result = await CheckReadiness(
        probes=[OkProbe("a"), OkProbe("b")],
        timeout_seconds=0.5,
    ).execute()
    assert result.ready is True
    assert [p.status for p in result.probes] == [ProbeOutcome.OK, ProbeOutcome.OK]


@pytest.mark.unit
async def test_failing_probe() -> None:
    result = await CheckReadiness(
        probes=[OkProbe("a"), FailingProbe("b")],
        timeout_seconds=0.5,
    ).execute()
    assert result.ready is False
    assert result.probes[1].status is ProbeOutcome.FAILED


@pytest.mark.unit
async def test_hanging_probe_times_out() -> None:
    result = await CheckReadiness(
        probes=[HangingProbe("slow")],
        timeout_seconds=0.05,
    ).execute()
    assert result.ready is False
    assert result.probes[0].status is ProbeOutcome.TIMEOUT


@pytest.mark.unit
async def test_mixed_outcomes() -> None:
    result = await CheckReadiness(
        probes=[OkProbe("a"), FailingProbe("b"), HangingProbe("c")],
        timeout_seconds=0.05,
    ).execute()
    assert result.ready is False
    assert [p.status for p in result.probes] == [
        ProbeOutcome.OK,
        ProbeOutcome.FAILED,
        ProbeOutcome.TIMEOUT,
    ]


@pytest.mark.unit
async def test_empty_probes_ready() -> None:
    result = await CheckReadiness(probes=[], timeout_seconds=0.5).execute()
    assert result.ready is True
    assert result.probes == ()
