"""Product-local hour key parts for LLM spend spike (DST-aware)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from svoi_pravila.adapters.cache.llm_budget import _local_hour_parts


@pytest.mark.unit
def test_local_hour_parts_follow_new_york_spring_forward() -> None:
    """02:xx is skipped on the spring-forward day; wall hour jumps 1 -> 3."""
    tz = "America/New_York"
    before = datetime(2026, 3, 8, 6, 30, tzinfo=UTC)
    day, hour, expire_unix = _local_hour_parts(before, tz)
    assert day == datetime(2026, 3, 8, tzinfo=ZoneInfo(tz)).date()
    assert hour == 1
    local_expire = datetime.fromtimestamp(expire_unix, tz=ZoneInfo(tz))
    assert local_expire.hour == 3
    assert local_expire.minute == 0

    # +1h UTC lands on 03:30 EDT (02:xx does not exist that morning).
    after = before + timedelta(hours=1)
    day2, hour2, _ = _local_hour_parts(after, tz)
    assert day2 == day
    assert hour2 == 3
    assert after.astimezone(ZoneInfo(tz)).hour == 3
