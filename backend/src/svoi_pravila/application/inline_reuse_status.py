"""C0 status vocabulary for inline result reuse."""

from __future__ import annotations

from enum import StrEnum


class InlineReuseStatus(StrEnum):
    """How an inline reuse lookup resolved for one waiter (C0)."""

    HIT = "hit"
    JOIN = "join"
    MISS = "miss"
