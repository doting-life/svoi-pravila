"""Reference calls, cumulative target users, priced cost and load peaks."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

import pytest

from svoi_pravila.domain.analytics import aggregate
from svoi_pravila.domain.enums import UsageOutcome, UsageScenario, UsageSurface
from svoi_pravila.domain.ids import UsageEventId
from svoi_pravila.domain.llm_pricing import LlmPrice
from svoi_pravila.domain.usage import UsageEvent

_TZ = "Europe/Moscow"
_COMPUTED = datetime(2026, 3, 20, 0, 30, tzinfo=UTC)
_LIGHT = "light"
_PRO = "pro"
_DAY = date(2026, 3, 15)
_PRICES = (
    LlmPrice(_LIGHT, date(2026, 3, 1), Decimal("0.0650")),
    LlmPrice(_LIGHT, date(2026, 3, 16), Decimal("0.0800")),
    LlmPrice(_PRO, date(2026, 3, 1), Decimal("0.5000")),
)


def _pseudo(n: int) -> str:
    return f"{n:064x}"


def _call(
    event_id: int,
    when: datetime,
    *,
    user: int = 1,
    model: str = _LIGHT,
    attempts: int = 1,
    billable: int = 1000,
    scenario: UsageScenario = UsageScenario.DECODE,
) -> UsageEvent:
    return UsageEvent(
        id=UsageEventId(UUID(int=event_id)),
        occurred_at=when,
        user_pseudonym=_pseudo(user),
        scenario=scenario,
        surface=UsageSurface.DM,
        outcome=UsageOutcome.OK,
        unavailable_kind=None,
        safety="ok",
        model=model,
        prompt_version="v1",
        latency_ms=10,
        ttfc_ms=2,
        attempts=attempts,
        input_tokens=billable // 2,
        output_tokens=billable - billable // 2,
        billable_tokens=billable,
    )


def _at(hour: int, minute: int, second: int, *, day: int = 15) -> datetime:
    return datetime(2026, 3, day, hour, minute, second, tzinfo=UTC)


@pytest.mark.unit
def test_calls_cost_and_unpriced_models() -> None:
    events = (
        _call(1, _at(9, 0, 5), attempts=2, billable=1500),
        _call(2, _at(9, 0, 6), model=_PRO, attempts=1, billable=200, user=2),
        _call(3, _at(9, 5, 0), model="mystery", attempts=3, billable=900, user=3),
    )
    result = aggregate(events, _TZ, _DAY, _COMPUTED, 50_000, _PRICES)
    assert result.daily.llm_calls == 6
    assert result.daily.llm_cost_rub == Decimal("0.1975")
    assert result.daily.unpriced_models == 1
    by_model = {row.model: row for row in result.llm}
    assert by_model[_LIGHT].price_rub_per_1k == Decimal("0.0650")
    assert by_model[_LIGHT].cost_rub == Decimal("0.0975")
    assert by_model[_LIGHT].llm_calls == 2
    assert by_model["mystery"].price_rub_per_1k is None
    assert by_model["mystery"].cost_rub is None


@pytest.mark.unit
def test_price_change_applies_from_its_day() -> None:
    event_before = _call(1, _at(9, 0, 0, day=15), billable=1000)
    event_after = _call(2, _at(9, 0, 0, day=16), billable=1000)
    events = (event_before, event_after)
    before = aggregate(events, _TZ, date(2026, 3, 15), _COMPUTED, 1, _PRICES)
    after = aggregate(events, _TZ, date(2026, 3, 16), _COMPUTED, 1, _PRICES)
    assert before.daily.llm_cost_rub == Decimal("0.0650")
    assert after.daily.llm_cost_rub == Decimal("0.0800")


@pytest.mark.unit
def test_without_prices_everything_is_unpriced() -> None:
    result = aggregate((_call(1, _at(9, 0, 0)),), _TZ, _DAY, _COMPUTED, 1)
    assert result.daily.llm_cost_rub == Decimal(0)
    assert result.daily.unpriced_models == 1
    assert result.llm[0].cost_rub is None


@pytest.mark.unit
def test_target_users_total_is_cumulative_and_counts_each_user_once() -> None:
    events = (
        _call(1, _at(9, 0, 0, day=14), user=1),
        _call(2, _at(9, 0, 0, day=15), user=1),
        _call(3, _at(9, 0, 0, day=15), user=2),
        _call(4, _at(9, 0, 0, day=16), user=3),
    )
    assert aggregate(events, _TZ, date(2026, 3, 14), _COMPUTED, 1).daily.target_users_total == 1
    assert aggregate(events, _TZ, date(2026, 3, 15), _COMPUTED, 1).daily.target_users_total == 2
    assert aggregate(events, _TZ, date(2026, 3, 16), _COMPUTED, 1).daily.target_users_total == 3


@pytest.mark.unit
def test_peaks_group_by_minute_and_second_and_tie_breaks_to_earliest_minute() -> None:
    events = (
        _call(1, _at(9, 1, 10), billable=300, attempts=1),
        _call(2, _at(9, 1, 10), billable=200, attempts=2, user=2),
        _call(3, _at(9, 1, 59), billable=100, attempts=1, user=3),
        _call(4, _at(9, 0, 0), billable=600, attempts=1, user=4),
        _call(5, _at(9, 2, 0), billable=600, attempts=1, user=5),
    )
    load = aggregate(events, _TZ, _DAY, _COMPUTED, 1).load
    assert load.peak_tpm == 600
    assert load.peak_tps == 600
    assert load.peak_llm_calls_per_minute == 4
    assert load.peak_llm_calls_per_second == 3
    assert load.peak_tpm_minute == _at(9, 0, 0)


@pytest.mark.unit
def test_day_without_calls_has_zero_peaks_and_no_minute() -> None:
    load = aggregate((), _TZ, _DAY, _COMPUTED, 1).load
    assert (load.peak_tpm, load.peak_tps) == (0, 0)
    assert (load.peak_llm_calls_per_minute, load.peak_llm_calls_per_second) == (0, 0)
    assert load.peak_tpm_minute is None
