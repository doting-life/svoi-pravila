"""Pure next_run / next_wake for the analytics scheduler."""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta

import pytest

from svoi_pravila.application.analytics_schedule import next_run, next_wake


@pytest.mark.unit
def test_next_run_moscow_same_day_then_next_day() -> None:
    run_at = time(3, 30)
    before = datetime(2026, 1, 15, 0, 0, tzinfo=UTC)
    assert next_run(before, "Europe/Moscow", run_at) == datetime(2026, 1, 15, 0, 30, tzinfo=UTC)
    after = datetime(2026, 1, 15, 0, 30, tzinfo=UTC)
    assert next_run(after, "Europe/Moscow", run_at) == datetime(2026, 1, 16, 0, 30, tzinfo=UTC)


@pytest.mark.unit
def test_next_run_berlin_spring_forward() -> None:
    run_at = time(3, 30)
    before_jump = datetime(2026, 3, 29, 0, 0, tzinfo=UTC)
    assert next_run(before_jump, "Europe/Berlin", run_at) == datetime(
        2026, 3, 29, 1, 30, tzinfo=UTC
    )
    after_jump = datetime(2026, 3, 29, 2, 0, tzinfo=UTC)
    assert next_run(after_jump, "Europe/Berlin", run_at) == datetime(2026, 3, 30, 1, 30, tzinfo=UTC)


@pytest.mark.unit
def test_next_run_berlin_fall_back() -> None:
    run_at = time(3, 30)
    morning = datetime(2026, 10, 25, 0, 0, tzinfo=UTC)
    got = next_run(morning, "Europe/Berlin", run_at)
    assert got == datetime(2026, 10, 25, 2, 30, tzinfo=UTC)
    later = datetime(2026, 10, 25, 3, 0, tzinfo=UTC)
    assert next_run(later, "Europe/Berlin", run_at) == datetime(2026, 10, 26, 2, 30, tzinfo=UTC)


@pytest.mark.unit
def test_next_wake_success_uses_daily_schedule() -> None:
    now = datetime(2026, 1, 15, 0, 31, tzinfo=UTC)
    run_at = time(3, 30)
    assert next_wake(now, "Europe/Moscow", run_at, 0) == next_run(now, "Europe/Moscow", run_at)


@pytest.mark.unit
def test_next_wake_failure_backoff_then_recovery() -> None:
    now = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)
    run_at = time(3, 30)
    assert next_wake(now, "Europe/Moscow", run_at, 1) == now + timedelta(minutes=5)
    assert next_wake(now, "Europe/Moscow", run_at, 2) == now + timedelta(minutes=15)
    assert next_wake(now, "Europe/Moscow", run_at, 3) == now + timedelta(minutes=60)
    assert next_wake(now, "Europe/Moscow", run_at, 4) == now + timedelta(minutes=60)
    assert next_wake(now, "Europe/Moscow", run_at, 0) == next_run(now, "Europe/Moscow", run_at)
