"""MetricsAnalyticsStore increments on terminal job_runs only."""

from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import UUID

import pytest
from prometheus_client import REGISTRY
from tests.fakes.analytics_store import FakeAnalyticsStore

from svoi_pravila.adapters.system.metrics_analytics_store import MetricsAnalyticsStore
from svoi_pravila.application.errors import AnalyticsJobName, AnalyticsJobStatus
from svoi_pravila.application.ports.analytics_store import JobRun


def _sample(labels: dict[str, str]) -> float:
    for family in REGISTRY.collect():
        for sample in family.samples:
            if sample.name != "sp_analytics_job_runs_total":
                continue
            if all(sample.labels.get(key) == value for key, value in labels.items()):
                return float(sample.value)
    return 0.0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_metrics_analytics_store_skips_running_and_counts_terminal() -> None:
    inner = FakeAnalyticsStore()
    store = MetricsAnalyticsStore(inner)
    now = datetime(2026, 3, 1, tzinfo=UTC)
    running = JobRun(
        id=UUID(int=1),
        job=AnalyticsJobName.PURGE,
        target_day=None,
        started_at=now,
        finished_at=None,
        status=AnalyticsJobStatus.RUNNING,
        rows_affected=0,
        error_kind=None,
    )
    succeeded = JobRun(
        id=UUID(int=2),
        job=AnalyticsJobName.PURGE,
        target_day=date(2026, 2, 28),
        started_at=now,
        finished_at=now,
        status=AnalyticsJobStatus.SUCCEEDED,
        rows_affected=3,
        error_kind=None,
    )
    before = _sample({"job": "purge", "status": "succeeded"})
    await store.record_run(running)
    assert _sample({"job": "purge", "status": "succeeded"}) == before
    await store.record_run(succeeded)
    assert _sample({"job": "purge", "status": "succeeded"}) == before + 1.0
