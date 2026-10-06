"""Pure next-run / retry wake instants for the in-process analytics scheduler."""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

_RETRY_DELAYS = (
    timedelta(minutes=5),
    timedelta(minutes=15),
    timedelta(minutes=60),
)


def next_run(now_utc: datetime, tz_name: str, run_at: time) -> datetime:
    """Return the next ``run_at`` in ``tz_name`` strictly after ``now_utc``, as UTC."""
    tz = ZoneInfo(tz_name)
    local = now_utc.astimezone(tz)
    today_at = datetime.combine(local.date(), run_at, tzinfo=tz)
    if local < today_at:
        candidate = today_at
    else:
        candidate = datetime.combine(local.date() + timedelta(days=1), run_at, tzinfo=tz)
    return candidate.astimezone(UTC)


def next_wake(
    now_utc: datetime,
    tz_name: str,
    run_at: time,
    consecutive_failures: int,
) -> datetime:
    """Daily ``run_at`` after success; fixed backoff after ``AnalyticsJobFailed``."""
    if consecutive_failures <= 0:
        return next_run(now_utc, tz_name, run_at)
    index = min(consecutive_failures - 1, len(_RETRY_DELAYS) - 1)
    return now_utc + _RETRY_DELAYS[index]
