"""In-memory QuotaGate and LlmBudget fakes for unit tests."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from svoi_pravila.application.errors import CacheUnavailable
from svoi_pravila.application.ports.llm_budget import BudgetExhausted, BudgetOk
from svoi_pravila.application.ports.quota_gate import (
    QuotaExhausted,
    QuotaReservation,
    Reserved,
)
from svoi_pravila.domain.enums import QuotaClass


class FakeQuotaGate:
    """Per-class daily quota reserve/refund in memory."""

    def __init__(
        self,
        *,
        limit: int = 20,
        cache_unavailable: CacheUnavailable | None = None,
    ) -> None:
        self._limit = limit
        self._cache_unavailable = cache_unavailable
        self._counts: dict[tuple[str, QuotaClass, date], int] = {}
        self._refunded: set[str] = set()
        self.reserve_calls: list[tuple[str, QuotaClass, date]] = []
        self.refund_calls: list[QuotaReservation] = []

    async def reserve(
        self, pseudonym: str, quota_class: QuotaClass, day: date
    ) -> Reserved | QuotaExhausted:
        self.reserve_calls.append((pseudonym, quota_class, day))
        if self._cache_unavailable is not None:
            raise self._cache_unavailable
        key = (pseudonym, quota_class, day)
        count = self._counts.get(key, 0)
        resets_at = datetime(day.year, day.month, day.day, tzinfo=UTC) + timedelta(days=1)
        resets_at = resets_at.replace(microsecond=0)
        if count >= self._limit:
            return QuotaExhausted(resets_at=resets_at)
        self._counts[key] = count + 1
        remaining = self._limit - self._counts[key]
        reservation = QuotaReservation(
            reservation_id=f"res-{len(self.reserve_calls)}",
            pseudonym=pseudonym,
            quota_class=quota_class,
            day=day,
            resets_at=resets_at,
        )
        return Reserved(remaining=remaining, resets_at=resets_at, reservation=reservation)

    async def refund(self, reservation: QuotaReservation) -> None:
        self.refund_calls.append(reservation)
        if reservation.reservation_id in self._refunded:
            return
        self._refunded.add(reservation.reservation_id)
        key = (reservation.pseudonym, reservation.quota_class, reservation.day)
        count = self._counts.get(key, 0)
        if count > 0:
            self._counts[key] = count - 1

    def reserve_count(self) -> int:
        """How many times ``reserve`` was called."""
        return len(self.reserve_calls)


class FakeLlmBudget:
    """Global daily token budget check/add in memory."""

    def __init__(
        self,
        *,
        exhausted: bool = False,
        check_unavailable: CacheUnavailable | None = None,
        add_unavailable: CacheUnavailable | None = None,
        cache_unavailable: CacheUnavailable | None = None,
    ) -> None:
        self._exhausted = exhausted
        self._check_unavailable = check_unavailable or cache_unavailable
        self._add_unavailable = add_unavailable or cache_unavailable
        self.check_calls: list[date] = []
        self.add_calls: list[tuple[date, int]] = []

    async def check(self, day: date) -> BudgetOk | BudgetExhausted:
        self.check_calls.append(day)
        if self._check_unavailable is not None:
            raise self._check_unavailable
        if self._exhausted:
            resets = datetime(day.year, day.month, day.day, tzinfo=UTC) + timedelta(days=1)
            return BudgetExhausted(resets_at=resets.replace(microsecond=0))
        return BudgetOk()

    async def add(self, day: date, billable_tokens: int) -> None:
        if self._add_unavailable is not None:
            raise self._add_unavailable
        self.add_calls.append((day, billable_tokens))

    def check_count(self) -> int:
        """How many times ``check`` was called."""
        return len(self.check_calls)
