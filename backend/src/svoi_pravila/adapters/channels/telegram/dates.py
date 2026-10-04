"""User-facing calendar dates in a configured IANA zone."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

_GENITIVE_MONTHS = (
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
)


def format_display_date(when: datetime, now: datetime, tz: ZoneInfo) -> str:
    """Russian genitive month; omit the year when it matches ``now`` in ``tz``."""
    local_when = when.astimezone(tz)
    local_now = now.astimezone(tz)
    month = _GENITIVE_MONTHS[local_when.month - 1]
    if local_when.year == local_now.year:
        return f"{local_when.day} {month}"
    return f"{local_when.day} {month} {local_when.year}"
