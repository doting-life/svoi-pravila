"""Appeal definition and aggregate oracle."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from uuid import UUID

import pytest

from svoi_pravila.domain.analytics import (
    aggregate,
    cohorts,
    is_appeal,
    percentile_cont,
)
from svoi_pravila.domain.enums import (
    Firmness,
    LimitKind,
    UsageEventKind,
    UsageOutcome,
    UsageScenario,
    UsageSurface,
)
from svoi_pravila.domain.ids import UsageEventId
from svoi_pravila.domain.usage import UsageEvent

_PSEUDO = "ab" * 32
_NOW = datetime(2026, 3, 15, 12, 0, 0, tzinfo=UTC)
_TZ = "Europe/Moscow"


def _generation(
    *,
    scenario: UsageScenario = UsageScenario.DECODE,
    surface: UsageSurface = UsageSurface.DM,
    outcome: UsageOutcome = UsageOutcome.OK,
    occurred_at: datetime = _NOW,
    user_pseudonym: str = _PSEUDO,
    event_id: int = 1,
    latency_ms: int = 10,
    ttfc_ms: int | None = 2,
    input_tokens: int = 1,
    output_tokens: int = 1,
    billable_tokens: int = 2,
) -> UsageEvent:
    if outcome is UsageOutcome.SCREENED:
        return UsageEvent(
            id=UsageEventId(UUID(int=event_id)),
            occurred_at=occurred_at,
            user_pseudonym=user_pseudonym,
            scenario=scenario,
            surface=surface,
            outcome=outcome,
            unavailable_kind=None,
            safety="crisis",
            model=None,
            prompt_version=None,
            latency_ms=latency_ms,
            ttfc_ms=None,
            attempts=0,
            input_tokens=0,
            output_tokens=0,
            billable_tokens=0,
        )
    return UsageEvent(
        id=UsageEventId(UUID(int=event_id)),
        occurred_at=occurred_at,
        user_pseudonym=user_pseudonym,
        scenario=scenario,
        surface=surface,
        outcome=outcome,
        unavailable_kind=None,
        safety="ok",
        model="m",
        prompt_version="v1",
        latency_ms=latency_ms,
        ttfc_ms=ttfc_ms,
        attempts=1,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        billable_tokens=billable_tokens,
    )


def _chosen(
    *,
    scenario: UsageScenario = UsageScenario.DECODE,
    occurred_at: datetime = _NOW,
    user_pseudonym: str = _PSEUDO,
    event_id: int = 1,
) -> UsageEvent:
    return UsageEvent(
        id=UsageEventId(UUID(int=event_id)),
        occurred_at=occurred_at,
        user_pseudonym=user_pseudonym,
        scenario=scenario,
        surface=UsageSurface.INLINE,
        outcome=UsageOutcome.OK,
        unavailable_kind=None,
        safety=None,
        model=None,
        prompt_version=None,
        latency_ms=0,
        ttfc_ms=None,
        attempts=0,
        input_tokens=0,
        output_tokens=0,
        billable_tokens=0,
        event_kind=UsageEventKind.RESULT_CHOSEN,
        variant_firmness=Firmness.GENTLE,
    )


def _limited(
    *,
    limit_kind: LimitKind,
    surface: UsageSurface = UsageSurface.DM,
    occurred_at: datetime = _NOW,
    user_pseudonym: str = _PSEUDO,
    event_id: int = 1,
) -> UsageEvent:
    return UsageEvent(
        id=UsageEventId(UUID(int=event_id)),
        occurred_at=occurred_at,
        user_pseudonym=user_pseudonym,
        scenario=UsageScenario.DECODE,
        surface=surface,
        outcome=UsageOutcome.LIMITED,
        unavailable_kind=None,
        safety=None,
        model=None,
        prompt_version=None,
        latency_ms=0,
        ttfc_ms=None,
        attempts=0,
        input_tokens=0,
        output_tokens=0,
        billable_tokens=0,
        limit_kind=limit_kind,
    )


def _expected_appeal(
    kind: UsageEventKind,
    surface: UsageSurface,
    outcome: UsageOutcome,
    scenario: UsageScenario,
) -> bool:
    if scenario is UsageScenario.SUGGEST_RULE:
        return False
    if kind is UsageEventKind.RESULT_CHOSEN:
        return True
    return (
        kind is UsageEventKind.GENERATION
        and surface in {UsageSurface.DM, UsageSurface.MINIAPP}
        and outcome in {UsageOutcome.OK, UsageOutcome.REFUSED, UsageOutcome.SCREENED}
    )


def _generation_cases() -> list[tuple[UsageEventKind, UsageSurface, UsageOutcome, UsageScenario]]:
    outcomes = (
        UsageOutcome.OK,
        UsageOutcome.INVALID_OUTPUT,
        UsageOutcome.REFUSED,
        UsageOutcome.UNAVAILABLE,
        UsageOutcome.SCREENED,
    )
    return [
        (UsageEventKind.GENERATION, surface, outcome, scenario)
        for scenario in UsageScenario
        for surface in UsageSurface
        for outcome in outcomes
    ]


def _chosen_cases() -> list[tuple[UsageEventKind, UsageSurface, UsageOutcome, UsageScenario]]:
    return [
        (UsageEventKind.RESULT_CHOSEN, UsageSurface.INLINE, UsageOutcome.OK, scenario)
        for scenario in UsageScenario
    ]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("kind", "surface", "outcome", "scenario"),
    [*_generation_cases(), *_chosen_cases()],
)
def test_is_appeal_truth_table(
    kind: UsageEventKind,
    surface: UsageSurface,
    outcome: UsageOutcome,
    scenario: UsageScenario,
) -> None:
    if kind is UsageEventKind.RESULT_CHOSEN:
        event = _chosen(scenario=scenario)
    else:
        event = _generation(scenario=scenario, surface=surface, outcome=outcome)
    assert is_appeal(event) is _expected_appeal(kind, surface, outcome, scenario)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("values", "p", "expected"),
    [
        ((), 0.5, None),
        ((7,), 0.5, 7.0),
        ((1, 2, 3, 4), 0.5, 2.5),
        ((10, 20, 30), 0.5, 20.0),
        ((10, 20, 30, 40), 0.95, 10 + 30 * 0.95),
    ],
)
def test_percentile_cont_linear(values: tuple[int, ...], p: float, expected: float | None) -> None:
    got = percentile_cont(values, p)
    if expected is None:
        assert got is None
    else:
        assert got == pytest.approx(expected)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("limit_kind", "surface"),
    [
        (LimitKind.USER_QUOTA, UsageSurface.DM),
        (LimitKind.GLOBAL_BUDGET, UsageSurface.MINIAPP),
        (LimitKind.USER_QUOTA, UsageSurface.INLINE),
    ],
)
def test_limited_events_are_not_appeals(limit_kind: LimitKind, surface: UsageSurface) -> None:
    event = _limited(limit_kind=limit_kind, surface=surface)
    assert is_appeal(event) is False


@pytest.mark.unit
def test_limited_events_are_not_generation_errors() -> None:
    limited_quota = _limited(limit_kind=LimitKind.USER_QUOTA, event_id=10)
    limited_budget = _limited(
        limit_kind=LimitKind.GLOBAL_BUDGET,
        surface=UsageSurface.MINIAPP,
        event_id=11,
        user_pseudonym="cd" * 32,
    )
    packed = (limited_quota, limited_budget)
    day_agg = aggregate(packed, _TZ, date(2026, 3, 15), _NOW)
    assert day_agg.daily.generations == 2
    assert day_agg.daily.generation_errors == 0
    assert day_agg.daily.appeals == 0
    assert day_agg.daily.active_users == 0


@pytest.mark.unit
def test_aggregate_splits_moscow_midnight_and_new_users() -> None:
    before_midnight = datetime(2026, 3, 15, 20, 59, 59, tzinfo=UTC)
    after_midnight = datetime(2026, 3, 15, 21, 0, 0, tzinfo=UTC)
    prior = _generation(
        occurred_at=datetime(2026, 3, 14, 12, 0, 0, tzinfo=UTC),
        user_pseudonym="aa" * 32,
        event_id=1,
    )
    returning = _generation(
        occurred_at=before_midnight,
        user_pseudonym="aa" * 32,
        event_id=2,
    )
    newbie = _generation(
        occurred_at=after_midnight,
        user_pseudonym="bb" * 32,
        event_id=3,
        surface=UsageSurface.MINIAPP,
    )
    inline_ok = _generation(
        occurred_at=before_midnight,
        user_pseudonym="cc" * 32,
        event_id=4,
        surface=UsageSurface.INLINE,
        latency_ms=100,
        ttfc_ms=40,
    )
    chosen = _chosen(occurred_at=before_midnight, user_pseudonym="cc" * 32, event_id=5)
    computed = datetime(2026, 3, 16, 0, 30, 0, tzinfo=UTC)
    packed = (prior, returning, newbie, inline_ok, chosen)
    first = aggregate(packed, _TZ, date(2026, 3, 15), computed)
    assert first.daily.active_users == 2
    assert first.daily.appeals == 2
    assert first.daily.new_users == 1
    assert first.daily.generations == 2
    assert first.daily.generation_errors == 0
    second = aggregate(packed, _TZ, date(2026, 3, 16), computed)
    assert second.daily.active_users == 1
    assert second.daily.new_users == 1
    inline_slice = next(
        row
        for row in first.scenarios
        if row.scenario is UsageScenario.DECODE and row.surface is UsageSurface.INLINE
    )
    assert inline_slice.appeals == 1
    assert inline_slice.chosen == 1
    assert inline_slice.ok == 1
    assert inline_slice.latency_p50_ms == 100.0


@pytest.mark.unit
def test_cohorts_null_until_windows_complete() -> None:
    c = datetime(2026, 3, 1, 12, 0, 0, tzinfo=UTC)
    d1 = c + timedelta(days=1)
    d7 = c + timedelta(days=7)
    events = (
        _generation(occurred_at=c, user_pseudonym="aa" * 32, event_id=1),
        _generation(occurred_at=d1, user_pseudonym="aa" * 32, event_id=2),
        _generation(occurred_at=d7, user_pseudonym="aa" * 32, event_id=3),
        _generation(occurred_at=c, user_pseudonym="bb" * 32, event_id=4),
    )
    incomplete = cohorts(
        events,
        _TZ,
        date(2026, 3, 1),
        date(2026, 3, 1),
        date(2026, 3, 1),
        _NOW,
    )
    assert len(incomplete) == 1
    assert incomplete[0].size == 2
    assert incomplete[0].d1_retained is None
    assert incomplete[0].d7_retained is None
    with_d1 = cohorts(
        events,
        _TZ,
        date(2026, 3, 1),
        date(2026, 3, 1),
        date(2026, 3, 2),
        _NOW,
    )
    assert with_d1[0].d1_retained == 1
    assert with_d1[0].d7_retained is None
    complete = cohorts(
        events,
        _TZ,
        date(2026, 3, 1),
        date(2026, 3, 1),
        date(2026, 3, 8),
        _NOW,
    )
    assert complete[0].d1_retained == 1
    assert complete[0].d7_retained == 1
    outsider = _generation(
        occurred_at=datetime(2026, 2, 1, 12, 0, 0, tzinfo=UTC),
        user_pseudonym="cc" * 32,
        event_id=5,
    )
    noise = _generation(
        occurred_at=c,
        user_pseudonym="dd" * 32,
        event_id=6,
        scenario=UsageScenario.SUGGEST_RULE,
    )
    filtered = cohorts(
        (*events, outsider, noise),
        _TZ,
        date(2026, 3, 1),
        date(2026, 3, 1),
        date(2026, 3, 8),
        _NOW,
    )
    assert filtered[0].size == 2
