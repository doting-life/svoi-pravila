"""Unit tests for cancel-time quota refund / budget charge helper."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from svoi_pravila.application.errors import CacheErrorKind, CacheUnavailable
from svoi_pravila.application.ports.quota_gate import QuotaReservation, Reserved
from svoi_pravila.application.use_cases._cancelled_budget import (
    CancelledGeneration,
    charge_cancelled_budget,
    handle_generation_cancelled,
)
from svoi_pravila.domain.enums import QuotaClass
from tests.fakes.quota_budget import FakeLlmBudget, FakeQuotaGate

_DAY = date(2026, 10, 7)


def _reservation() -> QuotaReservation:
    return QuotaReservation(
        reservation_id="res-1",
        pseudonym="ab" * 32,
        quota_class=QuotaClass.INLINE,
        day=_DAY,
        resets_at=datetime(2026, 10, 8, tzinfo=UTC),
    )


@pytest.mark.unit
async def test_handle_generation_cancelled_before_provider_refunds() -> None:
    gate = FakeQuotaGate(limit=10)
    budget = FakeLlmBudget()
    reserved = await gate.reserve("ab" * 32, QuotaClass.INLINE, _DAY)
    assert isinstance(reserved, Reserved)
    await handle_generation_cancelled(
        CancelledGeneration(
            provider_started=False,
            reservation=reserved.reservation,
            quota_gate=gate,
            llm_budget=budget,
            day=_DAY,
            billable_tokens=999,
        )
    )
    assert len(gate.refund_calls) == 1
    assert budget.add_calls == []


@pytest.mark.unit
async def test_handle_generation_cancelled_after_provider_charges() -> None:
    gate = FakeQuotaGate(limit=10)
    budget = FakeLlmBudget()
    await handle_generation_cancelled(
        CancelledGeneration(
            provider_started=True,
            reservation=_reservation(),
            quota_gate=gate,
            llm_budget=budget,
            day=_DAY,
            billable_tokens=420,
        )
    )
    assert gate.refund_calls == []
    assert budget.spent == 420


@pytest.mark.unit
async def test_handle_generation_cancelled_before_provider_without_reservation() -> None:
    budget = FakeLlmBudget()
    await handle_generation_cancelled(
        CancelledGeneration(
            provider_started=False,
            reservation=None,
            quota_gate=None,
            llm_budget=budget,
            day=_DAY,
            billable_tokens=10,
        )
    )
    assert budget.add_calls == []


@pytest.mark.unit
async def test_charge_cancelled_budget_logs_cache_unavailable() -> None:
    budget = FakeLlmBudget(add_unavailable=CacheUnavailable(CacheErrorKind.SERVER))
    await charge_cancelled_budget(budget, day=_DAY, billable_tokens=10)
    assert budget.add_calls == []


@pytest.mark.unit
async def test_handle_generation_cancelled_requires_gate_for_refund() -> None:
    with pytest.raises(ValueError, match="quota_gate"):
        await handle_generation_cancelled(
            CancelledGeneration(
                provider_started=False,
                reservation=_reservation(),
                quota_gate=None,
                llm_budget=FakeLlmBudget(),
                day=_DAY,
                billable_tokens=1,
            )
        )
