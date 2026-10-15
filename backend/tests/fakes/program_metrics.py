"""In-memory price store, metrics reader and package writer for use-case tests."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from pathlib import Path

from svoi_pravila.application.ports.metrics_package import TableFile, TextFile
from svoi_pravila.domain.analytics import (
    CohortTotals,
    DailyTotals,
    LlmModelTotals,
    LoadPeaks,
    ScenarioTotals,
)
from svoi_pravila.domain.llm_pricing import LlmPrice


class FakeLlmPriceStore:
    """Keeps prices keyed by ``(model, valid_from)``."""

    def __init__(self, prices: Sequence[LlmPrice] = ()) -> None:
        self.rows: dict[tuple[str, date], LlmPrice] = {
            (item.model, item.valid_from): item for item in prices
        }

    async def list_prices(self) -> tuple[LlmPrice, ...]:
        return tuple(self.rows[key] for key in sorted(self.rows))

    async def upsert_price(self, price: LlmPrice) -> None:
        self.rows[(price.model, price.valid_from)] = price


class FakeProgramMetricsReader:
    """Returns the configured rows and records the requested ranges."""

    def __init__(self) -> None:
        self.daily: tuple[DailyTotals, ...] = ()
        self.scenarios: tuple[ScenarioTotals, ...] = ()
        self.llm: tuple[LlmModelTotals, ...] = ()
        self.load: tuple[LoadPeaks, ...] = ()
        self.cohorts: tuple[CohortTotals, ...] = ()
        self.ranges: list[tuple[date, date]] = []

    async def fetch_daily(self, from_day: date, to_day: date) -> tuple[DailyTotals, ...]:
        self.ranges.append((from_day, to_day))
        return self.daily

    async def fetch_scenarios(self, from_day: date, to_day: date) -> tuple[ScenarioTotals, ...]:
        _ = from_day, to_day
        return self.scenarios

    async def fetch_llm(self, from_day: date, to_day: date) -> tuple[LlmModelTotals, ...]:
        _ = from_day, to_day
        return self.llm

    async def fetch_load(self, from_day: date, to_day: date) -> tuple[LoadPeaks, ...]:
        _ = from_day, to_day
        return self.load

    async def fetch_cohorts(self, from_day: date, to_day: date) -> tuple[CohortTotals, ...]:
        _ = from_day, to_day
        return self.cohorts


class FakePackageWriter:
    """Records what would be written."""

    def __init__(self) -> None:
        self.calls: list[tuple[Path, tuple[TableFile | TextFile, ...], bool]] = []

    async def write(
        self,
        out_dir: Path,
        files: Sequence[TableFile | TextFile],
        *,
        force: bool,
    ) -> None:
        self.calls.append((out_dir, tuple(files), force))
