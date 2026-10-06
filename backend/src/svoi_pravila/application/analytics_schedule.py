"""Pure next-run instant for the in-process analytics scheduler."""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo


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
