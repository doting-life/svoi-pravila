"""ExportProgramMetrics: range validation, package content, rounding and empty cells."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from svoi_pravila.application.errors import ProgramExportErrorKind, ProgramExportFailed
from svoi_pravila.application.ports.metrics_package import TableFile, TextFile
from svoi_pravila.application.use_cases._program_report import DEFINITIONS_TABLE
from svoi_pravila.application.use_cases.export_program_metrics import (
    EXPORT_MAX_DAYS,
    ExportedFile,
    ExportProgramMetrics,
    ExportProgramMetricsInput,
    ExportProgramMetricsPorts,
)
from svoi_pravila.domain.analytics import (
    CohortTotals,
    DailyTotals,
    LlmModelTotals,
    LoadPeaks,
    ScenarioTotals,
)
from svoi_pravila.domain.enums import UsageScenario, UsageSurface
from svoi_pravila.domain.llm_pricing import LlmPrice
from tests.fakes.clock import FakeClock
from tests.fakes.program_metrics import (
    FakeLlmPriceStore,
    FakePackageWriter,
    FakeProgramMetricsReader,
)

_FIRST = date(2026, 3, 15)
_LAST = date(2026, 3, 16)
_NOW = datetime(2026, 3, 20, 8, 15, 30, tzinfo=UTC)
_OUT = Path("out")
_LIGHT = "light"
_PRICES = (
    LlmPrice(_LIGHT, date(2026, 3, 1), Decimal("0.0650")),
    LlmPrice(_LIGHT, date(2026, 4, 1), Decimal("0.0900")),
)


def _daily(
    day: date,
    *,
    active: int,
    appeals: int,
    generations: int,
    errors: int,
    calls: int,
    tokens: int,
    cost: str,
    total: int,
) -> DailyTotals:
    return DailyTotals(
        day=day,
        active_users=active,
        appeals=appeals,
        new_users=1,
        generations=generations,
        generation_errors=errors,
        users_limited=0,
        billable_tokens=tokens,
        llm_budget_tokens=1000,
        llm_calls=calls,
        target_users_total=total,
        llm_cost_rub=Decimal(cost),
        unpriced_models=0,
        computed_at=_NOW,
    )


def _scenario() -> ScenarioTotals:
    return ScenarioTotals(
        day=_FIRST,
        scenario=UsageScenario.DECODE,
        surface=UsageSurface.DM,
        appeals=3,
        users=2,
        ok=3,
        refused=0,
        screened=0,
        invalid_output=0,
        unavailable=0,
        chosen=0,
        limited_user_quota=0,
        limited_global_budget=0,
        latency_p50_ms=12.5,
        latency_p95_ms=None,
        ttfc_p50_ms=None,
        ttfc_p95_ms=None,
        input_tokens=10,
        output_tokens=20,
        billable_tokens=30,
    )


def _llm(model: str, price: Decimal | None) -> LlmModelTotals:
    return LlmModelTotals(
        day=_FIRST,
        scenario=UsageScenario.DECODE,
        surface=UsageSurface.DM,
        model=model,
        generations=1,
        llm_calls=2,
        input_tokens=10,
        output_tokens=20,
        billable_tokens=30,
        price_rub_per_1k=price,
        cost_rub=None if price is None else Decimal("0.0020"),
    )


def _reader() -> FakeProgramMetricsReader:
    reader = FakeProgramMetricsReader()
    reader.daily = (
        _daily(
            _FIRST,
            active=3,
            appeals=4,
            generations=5,
            errors=1,
            calls=7,
            tokens=2880,
            cost="0.12345",
            total=3,
        ),
        _daily(
            _LAST,
            active=0,
            appeals=0,
            generations=0,
            errors=0,
            calls=0,
            tokens=0,
            cost="0",
            total=5,
        ),
    )
    reader.scenarios = (_scenario(),)
    reader.llm = (_llm(_LIGHT, Decimal("0.0650")), _llm("mystery", None))
    reader.load = (
        LoadPeaks(
            day=_FIRST,
            peak_tpm=900,
            peak_tps=300,
            peak_llm_calls_per_minute=4,
            peak_llm_calls_per_second=2,
            peak_tpm_minute=datetime(2026, 3, 15, 9, 1, tzinfo=UTC),
        ),
        LoadPeaks(
            day=_LAST,
            peak_tpm=0,
            peak_tps=0,
            peak_llm_calls_per_minute=0,
            peak_llm_calls_per_second=0,
            peak_tpm_minute=None,
        ),
        LoadPeaks(
            day=date(2026, 3, 17),
            peak_tpm=1,
            peak_tps=1,
            peak_llm_calls_per_minute=1,
            peak_llm_calls_per_second=1,
            peak_tpm_minute=None,
        ),
    )
    reader.cohorts = (
        CohortTotals(_FIRST, 3, 1, 2, _NOW),
        CohortTotals(_LAST, 2, 1, None, _NOW),
        CohortTotals(date(2026, 3, 17), 2, None, None, _NOW),
        CohortTotals(date(2026, 3, 18), 0, 0, 0, _NOW),
    )
    return reader


def _use_case(reader: FakeProgramMetricsReader, writer: FakePackageWriter) -> ExportProgramMetrics:
    return ExportProgramMetrics(
        ExportProgramMetricsPorts(
            reader=reader,
            prices=FakeLlmPriceStore(_PRICES),
            writer=writer,
            clock=FakeClock(_NOW),
        )
    )


def _command(
    from_day: date = _FIRST, to_day: date = _LAST, *, force: bool = False
) -> ExportProgramMetricsInput:
    return ExportProgramMetricsInput(from_day=from_day, to_day=to_day, out_dir=_OUT, force=force)


def _table(writer: FakePackageWriter, name: str) -> TableFile:
    (_, files, _force) = writer.calls[0]
    found = next(item for item in files if item.name == name)
    assert isinstance(found, TableFile)
    return found


@pytest.mark.unit
async def test_package_has_seven_files_in_order_and_reports_row_counts() -> None:
    writer = FakePackageWriter()
    exported = await _use_case(_reader(), writer).execute(_command(force=True))
    (out_dir, files, force) = writer.calls[0]
    assert out_dir == _OUT
    assert force is True
    assert [item.name for item in files] == [
        "00_definitions.md",
        "01_summary.csv",
        "02_daily.csv",
        "03_scenarios.csv",
        "04_llm_models.csv",
        "05_retention.csv",
        "06_load_peaks.csv",
    ]
    rows = {item.name: item.rows for item in exported}
    assert rows["01_summary.csv"] == 1
    assert rows["02_daily.csv"] == 2
    assert rows["03_scenarios.csv"] == 1
    assert rows["04_llm_models.csv"] == 2
    assert rows["05_retention.csv"] == 4
    assert rows["06_load_peaks.csv"] == 3
    assert isinstance(exported[0], ExportedFile)
    assert rows["00_definitions.md"] > 10


@pytest.mark.unit
async def test_definitions_file_has_table_period_time_and_prices_up_to_the_end() -> None:
    writer = FakePackageWriter()
    await _use_case(_reader(), writer).execute(_command())
    (_, files, _force) = writer.calls[0]
    text = files[0]
    assert isinstance(text, TextFile)
    assert DEFINITIONS_TABLE in text.content
    assert "Period: 2026-03-15 .. 2026-03-16" in text.content
    assert "Generated at: 2026-03-20T08:15:30+00:00" in text.content
    assert "| light | 2026-03-01 | 0.0650 |" in text.content
    assert "2026-04-01" not in text.content


@pytest.mark.unit
async def test_daily_file_ratios_and_money_are_rounded_to_two_decimals() -> None:
    writer = FakePackageWriter()
    await _use_case(_reader(), writer).execute(_command())
    daily = _table(writer, "02_daily.csv")
    assert daily.header[0] == "day"
    assert daily.rows[0] == (
        _FIRST,
        3,
        1,
        3,
        4,
        Decimal("1.33"),
        5,
        1,
        Decimal("0.20"),
        0,
        7,
        Decimal("2.33"),
        2880,
        Decimal("0.12"),
        Decimal("0.03"),
    )
    empty_day = daily.rows[1]
    assert empty_day[5] is None
    assert empty_day[8] is None
    assert empty_day[11] is None
    assert empty_day[14] is None
    assert empty_day[13] == Decimal("0.00")


@pytest.mark.unit
async def test_summary_uses_period_sums_not_means_of_daily_ratios() -> None:
    writer = FakePackageWriter()
    await _use_case(_reader(), writer).execute(_command())
    summary = _table(writer, "01_summary.csv")
    assert dict(zip(summary.header, summary.rows[0], strict=True)) == {
        "period_from": _FIRST,
        "period_to": _LAST,
        "days": 2,
        "target_users_total": 5,
        "active_user_days": 3,
        "target_actions": 4,
        "llm_calls": 7,
        "llm_calls_per_active_user": Decimal("2.33"),
        "solution_calls_per_active_user": Decimal("4.67"),
        "billable_tokens": 2880,
        "llm_cost_rub": Decimal("0.12"),
        "llm_cost_per_target_action_rub": Decimal("0.03"),
        "d1_retention_pct": Decimal("40.00"),
        "d7_retention_pct": Decimal("66.67"),
        "models": 2,
        "unpriced_models": 1,
    }


@pytest.mark.unit
async def test_empty_period_summary_has_empty_ratios() -> None:
    writer = FakePackageWriter()
    await _use_case(FakeProgramMetricsReader(), writer).execute(_command())
    summary = _table(writer, "01_summary.csv")
    row = dict(zip(summary.header, summary.rows[0], strict=True))
    assert row["days"] == 0
    assert row["target_users_total"] is None
    assert row["llm_calls_per_active_user"] is None
    assert row["solution_calls_per_active_user"] is None
    assert row["llm_cost_per_target_action_rub"] is None
    assert row["d1_retention_pct"] is None
    assert row["d7_retention_pct"] is None
    assert row["llm_cost_rub"] == Decimal("0.00")
    assert _table(writer, "02_daily.csv").rows == ()


@pytest.mark.unit
async def test_retention_percent_is_empty_until_observable() -> None:
    writer = FakePackageWriter()
    await _use_case(_reader(), writer).execute(_command())
    retention = _table(writer, "05_retention.csv")
    assert retention.rows[0][4:] == (Decimal("33.33"), Decimal("66.67"))
    assert retention.rows[1][4:] == (Decimal("50.00"), None)
    assert retention.rows[2][4:] == (None, None)
    assert retention.rows[3][4:] == (None, None)


@pytest.mark.unit
async def test_load_peaks_average_tpm_is_billable_tokens_over_a_day_of_minutes() -> None:
    writer = FakePackageWriter()
    await _use_case(_reader(), writer).execute(_command())
    load = _table(writer, "06_load_peaks.csv")
    assert load.rows[0][5] == datetime(2026, 3, 15, 9, 1, tzinfo=UTC)
    assert load.rows[0][6] == Decimal("2.00")
    assert load.rows[1][5:] == (None, Decimal("0.00"))
    assert load.rows[2][6] is None


@pytest.mark.unit
async def test_scenario_and_model_rows_keep_stored_values() -> None:
    writer = FakePackageWriter()
    await _use_case(_reader(), writer).execute(_command())
    scenario = dict(
        zip(
            _table(writer, "03_scenarios.csv").header,
            _table(writer, "03_scenarios.csv").rows[0],
            strict=True,
        )
    )
    assert scenario["scenario"] == "decode"
    assert scenario["surface"] == "dm"
    assert scenario["latency_p50_ms"] == 12.5
    assert scenario["latency_p95_ms"] is None
    models = _table(writer, "04_llm_models.csv").rows
    assert models[0][3:] == ("light", 1, 2, 10, 20, 30, Decimal("0.0650"), Decimal("0.0020"))
    assert models[1][-2:] == (None, None)


@pytest.mark.unit
async def test_range_is_forwarded_to_the_reader() -> None:
    reader = _reader()
    await _use_case(reader, FakePackageWriter()).execute(_command())
    assert reader.ranges == [(_FIRST, _LAST)]


@pytest.mark.unit
async def test_inverted_range_is_rejected_before_any_read() -> None:
    reader = _reader()
    writer = FakePackageWriter()
    with pytest.raises(ProgramExportFailed) as caught:
        await _use_case(reader, writer).execute(_command(_LAST, _FIRST))
    assert caught.value.kind is ProgramExportErrorKind.INVALID_RANGE
    assert reader.ranges == []
    assert writer.calls == []


@pytest.mark.unit
async def test_range_of_exactly_400_days_is_allowed_and_401_is_not() -> None:
    writer = FakePackageWriter()
    last_ok = _FIRST + timedelta(days=EXPORT_MAX_DAYS - 1)
    await _use_case(_reader(), writer).execute(_command(_FIRST, last_ok))
    assert len(writer.calls) == 1
    with pytest.raises(ProgramExportFailed) as caught:
        await _use_case(_reader(), writer).execute(
            _command(_FIRST, _FIRST + timedelta(days=EXPORT_MAX_DAYS))
        )
    assert caught.value.kind is ProgramExportErrorKind.RANGE_TOO_LONG
    assert len(writer.calls) == 1
