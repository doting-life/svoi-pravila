"""CLI argument validation and command wiring."""

from __future__ import annotations

from argparse import Namespace
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pytest
from tests.factories import make_settings

from svoi_pravila.application.errors import AnalyticsJobStatus
from svoi_pravila.cli.analytics import (
    BACKFILL_MAX_DAYS,
    _cmd_backfill,
    _cmd_purge,
    _cmd_run,
    _parser,
)


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
