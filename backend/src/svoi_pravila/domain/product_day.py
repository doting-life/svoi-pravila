"""Product-day calendar helpers (``SP_ANALYTICS_TIMEZONE``)."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from svoi_pravila.domain.time import require_utc

_EXPIRE_GRACE = timedelta(hours=1)


def product_day(moment: datetime, tz_name: str) -> date:
    """Calendar day of ``moment`` in ``tz_name``."""
    require_utc(moment)
    return moment.astimezone(ZoneInfo(tz_name)).date()


def day_start_utc(day: date, tz_name: str) -> datetime:
    """UTC instant of local midnight at the start of ``day``."""
    local = datetime(day.year, day.month, day.day, tzinfo=ZoneInfo(tz_name))
    return local.astimezone(ZoneInfo("UTC"))


def day_end_utc(day: date, tz_name: str) -> datetime:
    """UTC instant of local midnight at the start of the next calendar day."""
    return day_start_utc(day + timedelta(days=1), tz_name)


def resets_at_utc(day: date, tz_name: str) -> datetime:
    """When the product-day counters for ``day`` reset (start of the next day, UTC)."""
    return day_end_utc(day, tz_name)


def expire_at_utc(day: date, tz_name: str) -> datetime:
    """Valkey key expiry: one hour after the product day ends."""
    return day_end_utc(day, tz_name) + _EXPIRE_GRACE
