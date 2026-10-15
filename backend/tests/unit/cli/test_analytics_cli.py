"""CLI argument validation and command wiring."""

from __future__ import annotations

from argparse import Namespace
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from tests.factories import make_settings
from tests.fakes.clock import FakeClock
from tests.fakes.program_metrics import FakeLlmPriceStore, FakeProgramMetricsReader

from svoi_pravila.application.errors import AnalyticsJobStatus
from svoi_pravila.cli.analytics import (
    BACKFILL_MAX_DAYS,
    _cmd_backfill,
    _cmd_export,
    _cmd_prices_list,
    _cmd_prices_set,
    _cmd_purge,
    _cmd_run,
    _parser,
)
from svoi_pravila.domain.llm_pricing import LlmPrice


@pytest.mark.unit
def test_parser_requires_command() -> None:
    parser = _parser()
    with pytest.raises(SystemExit):
        parser.parse_args([])


@pytest.mark.unit
async def test_backfill_rejects_inverted_range() -> None:
    code = await _cmd_backfill(
        Namespace(from_day=date(2026, 2, 1), to_day=date(2026, 1, 1)),
        make_settings(),
    )
    assert code == 2


@pytest.mark.unit
async def test_backfill_rejects_span_over_400_days() -> None:
    start = date(2026, 1, 1)
    end = date(2026, 1, 1).fromordinal(start.toordinal() + BACKFILL_MAX_DAYS)
    code = await _cmd_backfill(
        Namespace(from_day=start, to_day=end),
        make_settings(),
    )
    assert code == 2


@pytest.mark.unit
async def test_run_disposes_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    disposed: list[str] = []
    engine = object()
    store = MagicMock()
    monkeypatch.setattr("svoi_pravila.cli.analytics.create_engine", lambda _s: engine)
    monkeypatch.setattr("svoi_pravila.cli.analytics.SqlAlchemyAnalyticsStore", lambda _e: store)

    async def _dispose(_engine: object) -> None:
        disposed.append("yes")

    monkeypatch.setattr("svoi_pravila.cli.analytics.dispose_engine", _dispose)

    class _Job:
        def __init__(self, _ports: object) -> None:
            return None

        async def execute(self, _now: object) -> None:
            return None

    monkeypatch.setattr("svoi_pravila.cli.analytics.RunDailyAnalytics", _Job)
    code = await _cmd_run(Namespace(), make_settings())
    assert code == 0
    assert disposed == ["yes"]


@pytest.mark.unit
async def test_purge_dry_run_prints_count(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    engine = object()
    store = MagicMock()
    store.count_usage_events_older_than = AsyncMock(return_value=7)
    monkeypatch.setattr("svoi_pravila.cli.analytics.create_engine", lambda _s: engine)
    monkeypatch.setattr("svoi_pravila.cli.analytics.SqlAlchemyAnalyticsStore", lambda _e: store)

    async def _dispose(_engine: object) -> None:
        return None

    monkeypatch.setattr("svoi_pravila.cli.analytics.dispose_engine", _dispose)
    code = await _cmd_purge(Namespace(dry_run=True), make_settings())
    assert code == 0
    assert capsys.readouterr().out.strip() == "7"


@pytest.mark.unit
async def test_purge_deletes(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    engine = object()
    store = MagicMock()
    store.purge_usage_events = AsyncMock(return_value=3)
    monkeypatch.setattr("svoi_pravila.cli.analytics.create_engine", lambda _s: engine)
    monkeypatch.setattr("svoi_pravila.cli.analytics.SqlAlchemyAnalyticsStore", lambda _e: store)

    async def _dispose(_engine: object) -> None:
        return None

    monkeypatch.setattr("svoi_pravila.cli.analytics.dispose_engine", _dispose)
    code = await _cmd_purge(Namespace(dry_run=False), make_settings())
    assert code == 0
    assert capsys.readouterr().out.strip() == "3"


class _FakeBackfillStore:
    def __init__(self, *, acquired: bool) -> None:
        self.acquired = acquired
        self.days: list[date] = []
        self.runs: list[object] = []
        self.cohorts = 0

    @asynccontextmanager
    async def hold_lock(self) -> AsyncIterator[bool]:
        yield self.acquired

    async def record_run(self, run: object) -> None:
        self.runs.append(run)

    async def ensure_timezone(self, tz_name: str) -> None:
        _ = tz_name

    async def compute_day(
        self, day: date, tz_name: str, computed_at: object, llm_budget_tokens: int
    ) -> int:
        _ = tz_name, computed_at, llm_budget_tokens
        self.days.append(day)
        return 1

    async def compute_cohorts(
        self,
        from_day: date,
        to_day: date,
        tz_name: str,
        as_of_day: date,
        computed_at: object,
    ) -> int:
        _ = from_day, to_day, tz_name, as_of_day, computed_at
        self.cohorts += 1
        return 2


def _patch_cli_store(monkeypatch: pytest.MonkeyPatch, store: _FakeBackfillStore) -> None:
    monkeypatch.setattr("svoi_pravila.cli.analytics.create_engine", lambda _s: object())
    monkeypatch.setattr("svoi_pravila.cli.analytics.SqlAlchemyAnalyticsStore", lambda _e: store)

    async def _dispose(_engine: object) -> None:
        return None

    monkeypatch.setattr("svoi_pravila.cli.analytics.dispose_engine", _dispose)


@pytest.mark.unit
async def test_backfill_skipped_when_locked(monkeypatch: pytest.MonkeyPatch) -> None:
    store = _FakeBackfillStore(acquired=False)
    _patch_cli_store(monkeypatch, store)
    code = await _cmd_backfill(
        Namespace(from_day=date(2026, 1, 1), to_day=date(2026, 1, 2)),
        make_settings(),
    )
    assert code == 0
    assert store.days == []
    assert store.runs
    assert getattr(store.runs[0], "status", None) is AnalyticsJobStatus.SKIPPED_LOCKED


@pytest.mark.unit
async def test_backfill_computes_inclusive_range(monkeypatch: pytest.MonkeyPatch) -> None:
    store = _FakeBackfillStore(acquired=True)
    _patch_cli_store(monkeypatch, store)
    code = await _cmd_backfill(
        Namespace(from_day=date(2026, 1, 1), to_day=date(2026, 1, 2)),
        make_settings(),
    )
    assert code == 0
    assert store.days == [date(2026, 1, 1), date(2026, 1, 2)]
    assert store.cohorts == 1


class _FakeProgramStore(FakeProgramMetricsReader, FakeLlmPriceStore):
    def __init__(self, prices: tuple[LlmPrice, ...] = ()) -> None:
        FakeProgramMetricsReader.__init__(self)
        FakeLlmPriceStore.__init__(self, prices)


def _patch_program_store(monkeypatch: pytest.MonkeyPatch, store: _FakeProgramStore) -> None:
    monkeypatch.setattr("svoi_pravila.cli.analytics.create_engine", lambda _s: object())
    monkeypatch.setattr(
        "svoi_pravila.cli.analytics.SqlAlchemyProgramMetricsStore", lambda _e: store
    )

    async def _dispose(_engine: object) -> None:
        return None

    monkeypatch.setattr("svoi_pravila.cli.analytics.dispose_engine", _dispose)


@pytest.mark.unit
def test_parser_accepts_prices_and_export_commands() -> None:
    parser = _parser()
    listed = parser.parse_args(["prices", "list"])
    assert listed.handler is _cmd_prices_list
    price = parser.parse_args(
        ["prices", "set", "--model", "m", "--rub-per-1k", "0.065", "--from", "2026-03-01"]
    )
    assert price.handler is _cmd_prices_set
    assert price.rub_per_1k == Decimal("0.065")
    export = parser.parse_args(
        ["export", "--from", "2026-03-01", "--to", "2026-03-02", "--out-dir", "o", "--force"]
    )
    assert export.handler is _cmd_export
    assert export.out_dir == Path("o")
    assert export.force is True


@pytest.mark.unit
def test_parser_rejects_a_non_numeric_price() -> None:
    with pytest.raises(SystemExit) as caught:
        _parser().parse_args(
            ["prices", "set", "--model", "m", "--rub-per-1k", "cheap", "--from", "2026-03-01"]
        )
    assert caught.value.code == 2


@pytest.mark.unit
async def test_prices_list_prints_a_table(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _patch_program_store(
        monkeypatch, _FakeProgramStore((LlmPrice("m", date(2026, 3, 1), Decimal("0.0650")),))
    )
    assert await _cmd_prices_list(Namespace(), make_settings()) == 0
    assert capsys.readouterr().out.splitlines() == [
        "model\tvalid_from\trub_per_1k_tokens",
        "m\t2026-03-01\t0.0650",
    ]


@pytest.mark.unit
@pytest.mark.parametrize("value", ["0", "-1", "0.00001", "NaN", "1000000"])
async def test_prices_set_rejects_invalid_price_with_exit_2(
    value: str, capsys: pytest.CaptureFixture[str]
) -> None:
    code = await _cmd_prices_set(
        Namespace(model="m", rub_per_1k=Decimal(value), from_day=date(2026, 3, 1)),
        make_settings(),
    )
    assert code == 2
    assert capsys.readouterr().err.startswith("prices set: ")


@pytest.mark.unit
async def test_prices_set_rejects_empty_model_with_exit_2() -> None:
    code = await _cmd_prices_set(
        Namespace(model="", rub_per_1k=Decimal(1), from_day=date(2026, 3, 1)), make_settings()
    )
    assert code == 2


@pytest.mark.unit
async def test_prices_set_stores_the_row_and_prints_the_backfill_command(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    store = _FakeProgramStore()
    _patch_program_store(monkeypatch, store)
    monkeypatch.setattr(
        "svoi_pravila.cli.analytics.SystemClock",
        lambda: FakeClock(datetime(2026, 3, 20, 12, 0, tzinfo=UTC)),
    )
    code = await _cmd_prices_set(
        Namespace(model="m", rub_per_1k=Decimal("0.07"), from_day=date(2026, 3, 1)),
        make_settings(),
    )
    assert code == 0
    assert ("m", date(2026, 3, 1)) in store.rows
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "price set: m 0.07 from 2026-03-01"
    assert out[1] == "backfill: svoi-pravila-analytics backfill --from 2026-03-01 --to 2026-03-19"


@pytest.mark.unit
async def test_prices_set_for_a_future_day_needs_no_backfill(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _patch_program_store(monkeypatch, _FakeProgramStore())
    monkeypatch.setattr(
        "svoi_pravila.cli.analytics.SystemClock",
        lambda: FakeClock(datetime(2026, 3, 20, 12, 0, tzinfo=UTC)),
    )
    code = await _cmd_prices_set(
        Namespace(model="m", rub_per_1k=Decimal("0.07"), from_day=date(2026, 4, 1)),
        make_settings(),
    )
    assert code == 0
    assert "backfill: not needed" in capsys.readouterr().out


def _is_empty(directory: Path) -> bool:
    return not any(directory.iterdir())


def _export_args(out_dir: Path, from_day: date, to_day: date, *, force: bool = False) -> Namespace:
    return Namespace(from_day=from_day, to_day=to_day, out_dir=out_dir, force=force)


@pytest.mark.unit
async def test_export_writes_the_package_and_prints_one_line_per_file(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    _patch_program_store(monkeypatch, _FakeProgramStore())
    args = _export_args(tmp_path, date(2026, 3, 1), date(2026, 3, 2))
    assert await _cmd_export(args, make_settings()) == 0
    lines = capsys.readouterr().out.splitlines()
    assert [line.split(":")[0] for line in lines] == [
        "00_definitions.md",
        "01_summary.csv",
        "02_daily.csv",
        "03_scenarios.csv",
        "04_llm_models.csv",
        "05_retention.csv",
        "06_load_peaks.csv",
    ]
    assert lines[1] == "01_summary.csv: 1 rows"
    assert (tmp_path / "02_daily.csv").exists()


@pytest.mark.unit
async def test_export_refuses_existing_files_without_force_and_accepts_force(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    _patch_program_store(monkeypatch, _FakeProgramStore())
    first, last = date(2026, 3, 1), date(2026, 3, 2)
    assert await _cmd_export(_export_args(tmp_path, first, last), make_settings()) == 0
    capsys.readouterr()
    assert await _cmd_export(_export_args(tmp_path, first, last), make_settings()) == 2
    assert "use --force" in capsys.readouterr().err
    assert await _cmd_export(_export_args(tmp_path, first, last, force=True), make_settings()) == 0


@pytest.mark.unit
@pytest.mark.parametrize(
    ("from_day", "to_day", "message"),
    [
        (date(2026, 3, 2), date(2026, 3, 1), "--to must be on or after --from"),
        (date(2026, 1, 1), date(2027, 3, 1), "range exceeds 400 days"),
    ],
)
async def test_export_bad_range_exits_2(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    from_day: date,
    to_day: date,
    message: str,
) -> None:
    _patch_program_store(monkeypatch, _FakeProgramStore())
    assert await _cmd_export(_export_args(tmp_path, from_day, to_day), make_settings()) == 2
    assert capsys.readouterr().err == f"export: {message}\n"
    assert _is_empty(tmp_path)


@pytest.mark.unit
async def test_export_write_failure_exits_1(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    _patch_program_store(monkeypatch, _FakeProgramStore())
    blocker = tmp_path / "file"
    blocker.write_text("x", encoding="utf-8")
    args = _export_args(blocker / "sub", date(2026, 3, 1), date(2026, 3, 2))
    assert await _cmd_export(args, make_settings()) == 1
    assert capsys.readouterr().err == "export: could not write to --out-dir\n"
