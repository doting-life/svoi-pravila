"""Per-user daily quota reserve/refund port (ADR-0009)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol

from svoi_pravila.domain.enums import QuotaClass


@dataclass(frozen=True, slots=True)
class QuotaReservation:
    """Opaque handle for a successful reserve (C0; no Telegram ids)."""

    reservation_id: str
    pseudonym: str
    quota_class: QuotaClass
    day: date
    resets_at: datetime


@dataclass(frozen=True, slots=True)
class Reserved:
    """Quota slot taken; ``remaining`` is slots left after this reserve."""

    remaining: int
    resets_at: datetime
    reservation: QuotaReservation


@dataclass(frozen=True, slots=True)
class QuotaExhausted:
    """Daily class quota is already at the limit."""

    resets_at: datetime


class QuotaGate(Protocol):
    """Atomic per-user daily quotas keyed by opaque pseudonyms."""

    async def reserve(
        self, pseudonym: str, quota_class: QuotaClass, day: date
    ) -> Reserved | QuotaExhausted:
        """INCR while below the class limit; return remaining or exhausted."""
        ...

    async def refund(self, reservation: QuotaReservation) -> None:
        """Return one slot; never below zero; idempotent per ``reservation_id``."""
        ...

    async def remaining(self, pseudonym: str, quota_class: QuotaClass, day: date) -> int:
        """Slots left today without mutating the counter (0 when exhausted)."""
        ...
