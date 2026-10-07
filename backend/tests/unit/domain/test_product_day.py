"""Product-day calendar helpers across fixed and DST timezones."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from svoi_pravila.domain.errors import InvalidTimestampError
from svoi_pravila.domain.product_day import (
    day_end_utc,
    day_start_utc,
    expire_at_utc,
    product_day,
    resets_at_utc,
)


@pytest.mark.unit
def test_product_day_europe_moscow_midnight() -> None:
    before = datetime(2026, 3, 15, 20, 59, 59, tzinfo=UTC)
    after = datetime(2026, 3, 15, 21, 0, 0, tzinfo=UTC)
    assert product_day(before, "Europe/Moscow") == date(2026, 3, 15)
    assert product_day(after, "Europe/Moscow") == date(2026, 3, 16)
    day = date(2026, 3, 15)
    start = day_start_utc(day, "Europe/Moscow")
    end = day_end_utc(day, "Europe/Moscow")
    assert start == datetime(2026, 3, 14, 21, 0, 0, tzinfo=UTC)
    assert end == datetime(2026, 3, 15, 21, 0, 0, tzinfo=UTC)
    assert resets_at_utc(day, "Europe/Moscow") == end
    assert expire_at_utc(day, "Europe/Moscow") == end + timedelta(hours=1)


@pytest.mark.unit
def test_product_day_rejects_naive() -> None:
    with pytest.raises(InvalidTimestampError):
        product_day(datetime(2026, 3, 15, 12, 0, 0), "Europe/Moscow")


@pytest.mark.unit
def test_product_day_berlin_spring_forward() -> None:
    """Europe/Berlin skips 02:00→03:00 on 2026-03-29; day bounds stay local midnights."""
    tz = "Europe/Berlin"
    day = date(2026, 3, 29)
    start = day_start_utc(day, tz)
    end = day_end_utc(day, tz)
    assert start == datetime(2026, 3, 28, 23, 0, 0, tzinfo=UTC)
    assert end == datetime(2026, 3, 29, 22, 0, 0, tzinfo=UTC)
    assert resets_at_utc(day, tz) == end
    assert expire_at_utc(day, tz) == end + timedelta(hours=1)
    before = datetime(2026, 3, 29, 0, 30, 0, tzinfo=UTC)
    after = datetime(2026, 3, 29, 22, 0, 0, tzinfo=UTC)
    assert product_day(before, tz) == date(2026, 3, 29)
    assert product_day(after, tz) == date(2026, 3, 30)


@pytest.mark.unit
def test_product_day_new_york_fall_back() -> None:
    """America/New_York repeats 01:00 on 2026-11-01; expire/resets use next local midnight."""
    tz = "America/New_York"
    day = date(2026, 11, 1)
    start = day_start_utc(day, tz)
    end = day_end_utc(day, tz)
    assert start == datetime(2026, 11, 1, 4, 0, 0, tzinfo=UTC)
    assert end == datetime(2026, 11, 2, 5, 0, 0, tzinfo=UTC)
    assert resets_at_utc(day, tz) == end
    assert expire_at_utc(day, tz) == end + timedelta(hours=1)
    during_first = datetime(2026, 11, 1, 5, 30, 0, tzinfo=UTC)
    after_end = datetime(2026, 11, 2, 5, 0, 0, tzinfo=UTC)
    assert product_day(during_first, tz) == date(2026, 11, 1)
    assert product_day(after_end, tz) == date(2026, 11, 2)
