"""UTC timestamp validation."""

from __future__ import annotations

from datetime import datetime, timedelta

from svoi_pravila.domain.errors import InvalidTimestampError


def require_utc(value: datetime) -> datetime:
    """Return ``value`` if timezone-aware UTC; otherwise raise ``InvalidTimestampError``."""
    if value.tzinfo is None:
        msg = "datetime must be timezone-aware"
        raise InvalidTimestampError(msg)
    if value.utcoffset() != timedelta(0):
        msg = "datetime must be UTC"
        raise InvalidTimestampError(msg)
    return value
