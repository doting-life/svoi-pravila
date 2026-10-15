"""Pure product-analytics reference: appeals, daily aggregates, and D1/D7 cohorts."""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from svoi_pravila.domain.enums import (
    LimitKind,
    UsageEventKind,
    UsageOutcome,
    UsageScenario,
    UsageSurface,
)
from svoi_pravila.domain.llm_pricing import (
    PRICE_QUANTUM,
    LlmPrice,
    effective_price,
    llm_cost,
    round_half_up,
)
from svoi_pravila.domain.usage import UsageEvent

_APPEAL_SURFACES = frozenset({UsageSurface.DM, UsageSurface.MINIAPP})
_APPEAL_OUTCOMES = frozenset({UsageOutcome.OK, UsageOutcome.REFUSED, UsageOutcome.SCREENED})
_ERROR_OUTCOMES = frozenset({UsageOutcome.UNAVAILABLE, UsageOutcome.INVALID_OUTPUT})

_Call = tuple[str, UsageEvent]
"""An LLM-call event (generation with a model) paired with its model name."""


def is_appeal(event: UsageEvent) -> bool:
    """Return whether ``event`` counts as a D-9 appeal."""
    if event.scenario is UsageScenario.SUGGEST_RULE:
        return False
    if event.event_kind is UsageEventKind.RESULT_CHOSEN:
        # Mini-app choice is bound to a generation that already counts as the appeal.
        return event.surface is UsageSurface.INLINE
    return (
        event.event_kind is UsageEventKind.GENERATION
        and event.surface in _APPEAL_SURFACES
        and event.outcome in _APPEAL_OUTCOMES
    )


def event_day(occurred_at: datetime, tz_name: str) -> date:
    """Calendar day of ``occurred_at`` in ``tz_name``."""
    return occurred_at.astimezone(ZoneInfo(tz_name)).date()


def percentile_cont(values: tuple[int, ...], p: float) -> float | None:
    """Linear interpolation matching Postgres ``percentile_cont``."""
    if not values:
        return None
    ordered = sorted(values)
    n = len(ordered)
    idx = (n - 1) * p
    lo = math.floor(idx)
    hi = math.ceil(idx)
    if lo == hi:
        return float(ordered[int(idx)])
    weight = idx - lo
    return float(ordered[lo]) * (1.0 - weight) + float(ordered[hi]) * weight


@dataclass(frozen=True, slots=True)
class DailyTotals:
    """One ``analytics_daily`` row (counts only)."""

    day: date
    active_users: int
    appeals: int
    new_users: int
    generations: int
    generation_errors: int
    users_limited: int
    billable_tokens: int
    llm_budget_tokens: int
    llm_calls: int
    target_users_total: int
    llm_cost_rub: Decimal
    unpriced_models: int
    computed_at: datetime


@dataclass(frozen=True, slots=True)
class ScenarioTotals:
    """One ``analytics_daily_scenario`` row (counts only)."""

    day: date
    scenario: UsageScenario
    surface: UsageSurface
    appeals: int
    users: int
    ok: int
    refused: int
    screened: int
    invalid_output: int
    unavailable: int
    chosen: int
    limited_user_quota: int
    limited_global_budget: int
    latency_p50_ms: float | None
    latency_p95_ms: float | None
    ttfc_p50_ms: float | None
    ttfc_p95_ms: float | None
    input_tokens: int
    output_tokens: int
    billable_tokens: int


@dataclass(frozen=True, slots=True)
class LlmModelTotals:
    """One ``analytics_daily_llm`` row (counts and roubles only)."""

    day: date
    scenario: UsageScenario
    surface: UsageSurface
    model: str
    generations: int
    llm_calls: int
    input_tokens: int
    output_tokens: int
    billable_tokens: int
    price_rub_per_1k: Decimal | None
    cost_rub: Decimal | None


@dataclass(frozen=True, slots=True)
class LoadPeaks:
    """One ``analytics_daily_load`` row: busiest minute and second of LLM traffic."""

    day: date
    peak_tpm: int
    peak_tps: int
    peak_llm_calls_per_minute: int
    peak_llm_calls_per_second: int
    peak_tpm_minute: datetime | None


@dataclass(frozen=True, slots=True)
class CohortTotals:
    """One ``analytics_cohorts`` row (counts only; retention may be NULL)."""

    cohort_day: date
    size: int
    d1_retained: int | None
    d7_retained: int | None
    computed_at: datetime


@dataclass(frozen=True, slots=True)
class DayAggregate:
    """Daily totals plus per-scenario/surface slices for one calendar day."""

    daily: DailyTotals
    scenarios: tuple[ScenarioTotals, ...]
    llm: tuple[LlmModelTotals, ...]
    load: LoadPeaks


def aggregate(
    events: tuple[UsageEvent, ...],
    tz_name: str,
    day: date,
    computed_at: datetime,
    llm_budget_tokens: int,
    prices: tuple[LlmPrice, ...] = (),
) -> DayAggregate:
    """Reference daily aggregates for ``day`` in ``tz_name`` priced with ``prices``."""
    day_events = tuple(event for event in events if event_day(event.occurred_at, tz_name) == day)
    appeals = tuple(event for event in day_events if is_appeal(event))
    appealers = {event.user_pseudonym for event in appeals}
    new_users = sum(
        1 for pseudo in appealers if not _had_appeal_before(events, tz_name, pseudo, day)
    )
    generations = tuple(
        event for event in day_events if event.event_kind is UsageEventKind.GENERATION
    )
    generation_errors = sum(1 for event in generations if event.outcome in _ERROR_OUTCOMES)
    users_limited = len(
        {
            event.user_pseudonym
            for event in generations
            if event.outcome is UsageOutcome.LIMITED and event.limit_kind is LimitKind.USER_QUOTA
        }
    )
    llm_calls: tuple[_Call, ...] = tuple(
        (event.model, event) for event in generations if event.model is not None
    )
    llm_rows = _llm_rows(llm_calls, day, prices)
    daily = DailyTotals(
        day=day,
        active_users=len(appealers),
        appeals=len(appeals),
        new_users=new_users,
        generations=len(generations),
        generation_errors=generation_errors,
        users_limited=users_limited,
        billable_tokens=sum(event.billable_tokens for event in generations),
        llm_budget_tokens=llm_budget_tokens,
        llm_calls=sum(event.attempts for _model, event in llm_calls),
        target_users_total=len(
            {
                event.user_pseudonym
                for event in events
                if is_appeal(event) and event_day(event.occurred_at, tz_name) <= day
            }
        ),
        llm_cost_rub=_day_cost(llm_calls, day, prices),
        unpriced_models=len(
            {model for model, _event in llm_calls if effective_price(prices, model, day) is None}
        ),
        computed_at=computed_at,
    )
    return DayAggregate(
        daily=daily,
        scenarios=_scenario_rows(day_events, day),
        llm=llm_rows,
        load=_load_peaks(llm_calls, day),
    )


def cohorts(
    events: tuple[UsageEvent, ...],
    tz_name: str,
    from_day: date,
    to_day: date,
    as_of_day: date,
    computed_at: datetime,
) -> tuple[CohortTotals, ...]:
    """Reference cohorts whose first stored appeal falls in ``[from_day, to_day]``."""
    first_days: dict[str, date] = {}
    for event in sorted(events, key=lambda item: item.occurred_at):
        if not is_appeal(event):
            continue
        if event.user_pseudonym not in first_days:
            first_days[event.user_pseudonym] = event_day(event.occurred_at, tz_name)
    members_by_day: dict[date, list[str]] = defaultdict(list)
    for pseudo, cohort_day in first_days.items():
        if from_day <= cohort_day <= to_day:
            members_by_day[cohort_day].append(pseudo)
    appeal_days: dict[str, set[date]] = defaultdict(set)
    for event in events:
        if is_appeal(event):
            appeal_days[event.user_pseudonym].add(event_day(event.occurred_at, tz_name))
    rows: list[CohortTotals] = []
    for cohort_day in sorted(members_by_day):
        members = tuple(members_by_day[cohort_day])
        d1_day = cohort_day + timedelta(days=1)
        d7_day = cohort_day + timedelta(days=7)
        d1 = (
            sum(1 for pseudo in members if d1_day in appeal_days[pseudo])
            if d1_day <= as_of_day
            else None
        )
        d7 = (
            sum(1 for pseudo in members if d7_day in appeal_days[pseudo])
            if d7_day <= as_of_day
            else None
        )
        rows.append(
            CohortTotals(
                cohort_day=cohort_day,
                size=len(members),
                d1_retained=d1,
                d7_retained=d7,
                computed_at=computed_at,
            )
        )
    return tuple(rows)


def _had_appeal_before(
    events: tuple[UsageEvent, ...],
    tz_name: str,
    user_pseudonym: str,
    day: date,
) -> bool:
    return any(
        is_appeal(event)
        and event.user_pseudonym == user_pseudonym
        and event_day(event.occurred_at, tz_name) < day
        for event in events
    )


def _scenario_rows(day_events: tuple[UsageEvent, ...], day: date) -> tuple[ScenarioTotals, ...]:
    grouped: dict[tuple[UsageScenario, UsageSurface], list[UsageEvent]] = defaultdict(list)
    for event in day_events:
        grouped[(event.scenario, event.surface)].append(event)
    rows: list[ScenarioTotals] = []
    for scenario, surface in sorted(grouped, key=lambda key: (key[0].value, key[1].value)):
        slice_events = tuple(grouped[(scenario, surface)])
        rows.append(_slice_totals(day, scenario, surface, slice_events))
    return tuple(rows)


def _slice_totals(
    day: date,
    scenario: UsageScenario,
    surface: UsageSurface,
    slice_events: tuple[UsageEvent, ...],
) -> ScenarioTotals:
    appeals = tuple(event for event in slice_events if is_appeal(event))
    generations = tuple(
        event for event in slice_events if event.event_kind is UsageEventKind.GENERATION
    )
    ok_generations = tuple(event for event in generations if event.outcome is UsageOutcome.OK)
    latencies = tuple(event.latency_ms for event in ok_generations)
    ttfcs = tuple(event.ttfc_ms for event in ok_generations if event.ttfc_ms is not None)
    return ScenarioTotals(
        day=day,
        scenario=scenario,
        surface=surface,
        appeals=len(appeals),
        users=len({event.user_pseudonym for event in appeals}),
        ok=sum(1 for event in generations if event.outcome is UsageOutcome.OK),
        refused=sum(1 for event in generations if event.outcome is UsageOutcome.REFUSED),
        screened=sum(1 for event in generations if event.outcome is UsageOutcome.SCREENED),
        invalid_output=sum(
            1 for event in generations if event.outcome is UsageOutcome.INVALID_OUTPUT
        ),
        unavailable=sum(1 for event in generations if event.outcome is UsageOutcome.UNAVAILABLE),
        chosen=sum(
            1
            for event in slice_events
            if event.event_kind is UsageEventKind.RESULT_CHOSEN
            and event.surface is UsageSurface.INLINE
        ),
        limited_user_quota=sum(
            1
            for event in generations
            if event.outcome is UsageOutcome.LIMITED and event.limit_kind is LimitKind.USER_QUOTA
        ),
        limited_global_budget=sum(
            1
            for event in generations
            if event.outcome is UsageOutcome.LIMITED and event.limit_kind is LimitKind.GLOBAL_BUDGET
        ),
        latency_p50_ms=percentile_cont(latencies, 0.5),
        latency_p95_ms=percentile_cont(latencies, 0.95),
        ttfc_p50_ms=percentile_cont(ttfcs, 0.5),
        ttfc_p95_ms=percentile_cont(ttfcs, 0.95),
        input_tokens=sum(event.input_tokens for event in generations),
        output_tokens=sum(event.output_tokens for event in generations),
        billable_tokens=sum(event.billable_tokens for event in generations),
    )


def _llm_rows(
    llm_calls: tuple[_Call, ...],
    day: date,
    prices: tuple[LlmPrice, ...],
) -> tuple[LlmModelTotals, ...]:
    grouped: dict[tuple[UsageScenario, UsageSurface, str], list[UsageEvent]] = defaultdict(list)
    for model, event in llm_calls:
        grouped[(event.scenario, event.surface, model)].append(event)
    rows: list[LlmModelTotals] = []
    for scenario, surface, model in sorted(
        grouped, key=lambda key: (key[0].value, key[1].value, key[2])
    ):
        slice_events = grouped[(scenario, surface, model)]
        billable = sum(event.billable_tokens for event in slice_events)
        price = effective_price(prices, model, day)
        rows.append(
            LlmModelTotals(
                day=day,
                scenario=scenario,
                surface=surface,
                model=model,
                generations=len(slice_events),
                llm_calls=sum(event.attempts for event in slice_events),
                input_tokens=sum(event.input_tokens for event in slice_events),
                output_tokens=sum(event.output_tokens for event in slice_events),
                billable_tokens=billable,
                price_rub_per_1k=price,
                cost_rub=(
                    None
                    if price is None
                    else round_half_up(llm_cost(billable, price), PRICE_QUANTUM)
                ),
            )
        )
    return tuple(rows)


def _day_cost(llm_calls: tuple[_Call, ...], day: date, prices: tuple[LlmPrice, ...]) -> Decimal:
    tokens_by_model: dict[str, int] = defaultdict(int)
    for model, event in llm_calls:
        tokens_by_model[model] += event.billable_tokens
    total = Decimal(0)
    for model, tokens in tokens_by_model.items():
        price = effective_price(prices, model, day)
        if price is not None:
            total += llm_cost(tokens, price)
    return round_half_up(total, PRICE_QUANTUM)


def _load_peaks(llm_calls: tuple[_Call, ...], day: date) -> LoadPeaks:
    tokens_per_minute: dict[datetime, int] = defaultdict(int)
    calls_per_minute: dict[datetime, int] = defaultdict(int)
    tokens_per_second: dict[datetime, int] = defaultdict(int)
    calls_per_second: dict[datetime, int] = defaultdict(int)
    for _model, event in llm_calls:
        second = event.occurred_at.astimezone(UTC).replace(microsecond=0)
        minute = second.replace(second=0)
        tokens_per_minute[minute] += event.billable_tokens
        calls_per_minute[minute] += event.attempts
        tokens_per_second[second] += event.billable_tokens
        calls_per_second[second] += event.attempts
    peak_tpm = max(tokens_per_minute.values(), default=0)
    peak_minute = min(
        (minute for minute, tokens in tokens_per_minute.items() if tokens == peak_tpm),
        default=None,
    )
    return LoadPeaks(
        day=day,
        peak_tpm=peak_tpm,
        peak_tps=max(tokens_per_second.values(), default=0),
        peak_llm_calls_per_minute=max(calls_per_minute.values(), default=0),
        peak_llm_calls_per_second=max(calls_per_second.values(), default=0),
        peak_tpm_minute=peak_minute,
    )
