"""Pure builders of the program-metrics package files from stored aggregates."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime
from decimal import Decimal

from svoi_pravila.application.ports.metrics_package import Cell, TableFile, TextFile
from svoi_pravila.domain.analytics import (
    CohortTotals,
    DailyTotals,
    LlmModelTotals,
    LoadPeaks,
    ScenarioTotals,
)
from svoi_pravila.domain.llm_pricing import LlmPrice, round_half_up

DEFINITIONS_NAME = "00_definitions.md"
SUMMARY_NAME = "01_summary.csv"
DAILY_NAME = "02_daily.csv"
SCENARIOS_NAME = "03_scenarios.csv"
LLM_MODELS_NAME = "04_llm_models.csv"
RETENTION_NAME = "05_retention.csv"
LOAD_PEAKS_NAME = "06_load_peaks.csv"

MINUTES_PER_DAY = 1440
_TWO_PLACES = Decimal("0.01")
_HUNDRED = Decimal(100)
_CALLS_PER_SOLUTION_CALL = 2

_TIMES = "\u00d7"
_DEFINITIONS: tuple[tuple[str, str], ...] = (
    (
        "Target action",
        "an appeal (D-9): `result_chosen`, or `generation` on `dm`/`miniapp` with outcome "
        "`ok`, `refused` or `screened`; `suggest_rule` excluded",
    ),
    (
        "Target users (cumulative)",
        "distinct pseudonyms whose first appeal among stored events is on or before the day "
        "(= running sum of cohort sizes)",
    ),
    ("Active user (DAU)", "unchanged (\u00a78)"),
    (
        "LLM call",
        "one provider call: \u03a3 `attempts` over `generation` events with non-null `model` "
        "(retries and both decode phases counted)",
    ),
    (
        "Calls to the Solution",
        f"LLM call + its versioned prompt (skill) = 2 {_TIMES} LLM calls; "
        "the product calls no other tools",
    ),
    (
        "LLM calls per active user",
        "LLM calls of the day / DAU of the day; for a period \u2014 \u03a3 calls / \u03a3 DAU",
    ),
    (
        "LLM cost, \u20bd",
        f"\u03a3 over models of `billable_tokens` {_TIMES} price of the model "
        "effective on that day / 1000",
    ),
    (
        "LLM cost per target action, \u20bd",
        "LLM cost / target actions (same day or period)",
    ),
    (
        "Peak TPM / TPS",
        "per day, maximum of \u03a3 `billable_tokens` of LLM calls grouped by minute / by second "
        "of `occurred_at` (event time = call completion); peak calls per minute / per second "
        "the same with \u03a3 `attempts`; the minute of the TPM peak is kept",
    ),
)
DEFINITIONS_TABLE = "| Metric | Definition |\n|---|---|\n" + "".join(
    f"| {metric} | {definition} |\n" for metric, definition in _DEFINITIONS
)

_CONVENTIONS = """\
- Ratios and money are rounded to 2 decimals (halves away from zero); a ratio with a zero
  denominator is an empty cell. Stored aggregates keep 4 decimals for money.
- `error_rate` is a share between 0 and 1: `generation_errors` / `generations`.
- `d1_pct`, `d7_pct` and the retention columns of `01_summary.csv` are percentages of the
  cohort size; the summary divides \u03a3 retained by \u03a3 size over the cohorts whose
  retention is already observable.
- `01_summary.csv`: `days` counts days with stored aggregates in the period; `target_users_total`
  is the value on the last such day; `models` and `unpriced_models` count distinct models in
  `04_llm_models.csv` (unpriced = no effective price on some day).
- Days and the time zone follow the analytics job; timestamps are UTC ISO-8601.
"""


def _ratio(numerator: int | Decimal, denominator: int | Decimal) -> Decimal | None:
    """``numerator / denominator`` to 2 decimals; ``None`` when the denominator is zero."""
    if denominator == 0:
        return None
    return round_half_up(Decimal(numerator) / Decimal(denominator), _TWO_PLACES)


def _percent(numerator: int, denominator: int) -> Decimal | None:
    """Percentage to 2 decimals; ``None`` when the denominator is zero."""
    if denominator == 0:
        return None
    return round_half_up(Decimal(numerator) * _HUNDRED / Decimal(denominator), _TWO_PLACES)


def _money(value: Decimal) -> Decimal:
    return round_half_up(value, _TWO_PLACES)


def _observed_percent(
    cohorts: tuple[CohortTotals, ...], retained: Callable[[CohortTotals], int | None]
) -> Decimal | None:
    retained_sum = 0
    size_sum = 0
    for cohort in cohorts:
        value = retained(cohort)
        if value is not None:
            retained_sum += value
            size_sum += cohort.size
    return _percent(retained_sum, size_sum)


def definitions_file(
    from_day: date,
    to_day: date,
    generated_at: datetime,
    prices: tuple[LlmPrice, ...],
) -> TextFile:
    """``00_definitions.md``: definitions, period, generation time, price table used."""
    price_rows = "\n".join(
        f"| {price.model} | {price.valid_from.isoformat()} | {price.rub_per_1k_tokens} |"
        for price in prices
        if price.valid_from <= to_day
    )
    content = (
        "# Program metrics: definitions\n\n"
        f"Period: {from_day.isoformat()} .. {to_day.isoformat()}\n\n"
        f"Generated at: {generated_at.isoformat(timespec='seconds')}\n\n"
        "## Definitions\n\n"
        f"{DEFINITIONS_TABLE}\n"
        "## Prices used (roubles per 1000 billable tokens)\n\n"
        "| Model | Valid from | Price |\n|---|---|---|\n"
        f"{price_rows}\n\n"
        "## Conventions\n\n"
        f"{_CONVENTIONS}"
    )
    return TextFile(name=DEFINITIONS_NAME, content=content)


def summary_file(
    from_day: date,
    to_day: date,
    daily: tuple[DailyTotals, ...],
    llm: tuple[LlmModelTotals, ...],
    cohorts: tuple[CohortTotals, ...],
) -> TableFile:
    """``01_summary.csv``: one row for the whole period."""
    active_user_days = sum(item.active_users for item in daily)
    target_actions = sum(item.appeals for item in daily)
    llm_calls = sum(item.llm_calls for item in daily)
    cost = sum((item.llm_cost_rub for item in daily), Decimal(0))
    row: tuple[Cell, ...] = (
        from_day,
        to_day,
        len(daily),
        daily[-1].target_users_total if daily else None,
        active_user_days,
        target_actions,
        llm_calls,
        _ratio(llm_calls, active_user_days),
        _ratio(_CALLS_PER_SOLUTION_CALL * llm_calls, active_user_days),
        sum(item.billable_tokens for item in daily),
        _money(cost),
        _ratio(cost, target_actions),
        _observed_percent(cohorts, lambda item: item.d1_retained),
        _observed_percent(cohorts, lambda item: item.d7_retained),
        len({item.model for item in llm}),
        len({item.model for item in llm if item.price_rub_per_1k is None}),
    )
    return TableFile(
        name=SUMMARY_NAME,
        header=(
            "period_from",
            "period_to",
            "days",
            "target_users_total",
            "active_user_days",
            "target_actions",
            "llm_calls",
            "llm_calls_per_active_user",
            "solution_calls_per_active_user",
            "billable_tokens",
            "llm_cost_rub",
            "llm_cost_per_target_action_rub",
            "d1_retention_pct",
            "d7_retention_pct",
            "models",
            "unpriced_models",
        ),
        rows=(row,),
    )


def daily_file(daily: tuple[DailyTotals, ...]) -> TableFile:
    """``02_daily.csv``: one row per stored day."""
    return TableFile(
        name=DAILY_NAME,
        header=(
            "day",
            "dau",
            "new_users",
            "target_users_total",
            "target_actions",
            "target_actions_per_dau",
            "generations",
            "generation_errors",
            "error_rate",
            "users_limited",
            "llm_calls",
            "llm_calls_per_active_user",
            "billable_tokens",
            "llm_cost_rub",
            "llm_cost_per_target_action_rub",
        ),
        rows=tuple(
            (
                item.day,
                item.active_users,
                item.new_users,
                item.target_users_total,
                item.appeals,
                _ratio(item.appeals, item.active_users),
                item.generations,
                item.generation_errors,
                _ratio(item.generation_errors, item.generations),
                item.users_limited,
                item.llm_calls,
                _ratio(item.llm_calls, item.active_users),
                item.billable_tokens,
                _money(item.llm_cost_rub),
                _ratio(item.llm_cost_rub, item.appeals),
            )
            for item in daily
        ),
    )


def scenarios_file(scenarios: tuple[ScenarioTotals, ...]) -> TableFile:
    """``03_scenarios.csv``: ``analytics_daily_scenario`` columns as stored."""
    return TableFile(
        name=SCENARIOS_NAME,
        header=(
            "day",
            "scenario",
            "surface",
            "appeals",
            "users",
            "ok",
            "refused",
            "screened",
            "invalid_output",
            "unavailable",
            "chosen",
            "limited_user_quota",
            "limited_global_budget",
            "latency_p50_ms",
            "latency_p95_ms",
            "ttfc_p50_ms",
            "ttfc_p95_ms",
            "input_tokens",
            "output_tokens",
            "billable_tokens",
        ),
        rows=tuple(
            (
                item.day,
                item.scenario.value,
                item.surface.value,
                item.appeals,
                item.users,
                item.ok,
                item.refused,
                item.screened,
                item.invalid_output,
                item.unavailable,
                item.chosen,
                item.limited_user_quota,
                item.limited_global_budget,
                item.latency_p50_ms,
                item.latency_p95_ms,
                item.ttfc_p50_ms,
                item.ttfc_p95_ms,
                item.input_tokens,
                item.output_tokens,
                item.billable_tokens,
            )
            for item in scenarios
        ),
    )


def llm_models_file(llm: tuple[LlmModelTotals, ...]) -> TableFile:
    """``04_llm_models.csv``: ``analytics_daily_llm`` columns as stored."""
    return TableFile(
        name=LLM_MODELS_NAME,
        header=(
            "day",
            "scenario",
            "surface",
            "model",
            "generations",
            "llm_calls",
            "input_tokens",
            "output_tokens",
            "billable_tokens",
            "price_rub_per_1k",
            "cost_rub",
        ),
        rows=tuple(
            (
                item.day,
                item.scenario.value,
                item.surface.value,
                item.model,
                item.generations,
                item.llm_calls,
                item.input_tokens,
                item.output_tokens,
                item.billable_tokens,
                item.price_rub_per_1k,
                item.cost_rub,
            )
            for item in llm
        ),
    )


def retention_file(cohorts: tuple[CohortTotals, ...]) -> TableFile:
    """``05_retention.csv``: cohort sizes with D1/D7 retention in percent."""
    return TableFile(
        name=RETENTION_NAME,
        header=("cohort_day", "size", "d1_retained", "d7_retained", "d1_pct", "d7_pct"),
        rows=tuple(
            (
                item.cohort_day,
                item.size,
                item.d1_retained,
                item.d7_retained,
                None if item.d1_retained is None else _percent(item.d1_retained, item.size),
                None if item.d7_retained is None else _percent(item.d7_retained, item.size),
            )
            for item in cohorts
        ),
    )


def load_peaks_file(load: tuple[LoadPeaks, ...], daily: tuple[DailyTotals, ...]) -> TableFile:
    """``06_load_peaks.csv``: peaks per day with the average tokens per minute."""
    billable_by_day = {item.day: item.billable_tokens for item in daily}
    return TableFile(
        name=LOAD_PEAKS_NAME,
        header=(
            "day",
            "peak_tpm",
            "peak_tps",
            "peak_llm_calls_per_minute",
            "peak_llm_calls_per_second",
            "peak_tpm_minute",
            "avg_tpm",
        ),
        rows=tuple(
            (
                item.day,
                item.peak_tpm,
                item.peak_tps,
                item.peak_llm_calls_per_minute,
                item.peak_llm_calls_per_second,
                item.peak_tpm_minute,
                (
                    _ratio(billable_by_day[item.day], MINUTES_PER_DAY)
                    if item.day in billable_by_day
                    else None
                ),
            )
            for item in load
        ),
    )
