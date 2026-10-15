"""Export the accelerator's program-metrics package from stored daily aggregates."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

from svoi_pravila.application.errors import ProgramExportErrorKind, ProgramExportFailed
from svoi_pravila.application.ports.clock import Clock
from svoi_pravila.application.ports.llm_price_store import LlmPriceStore
from svoi_pravila.application.ports.metrics_package import (
    MetricsPackageWriter,
    TableFile,
    TextFile,
)
from svoi_pravila.application.ports.program_metrics import ProgramMetricsReader
from svoi_pravila.application.use_cases import _program_report as report

EXPORT_MAX_DAYS = 400


@dataclass(frozen=True, slots=True)
class ExportProgramMetricsPorts:
    """Collaborators for ``ExportProgramMetrics``."""

    reader: ProgramMetricsReader
    prices: LlmPriceStore
    writer: MetricsPackageWriter
    clock: Clock


@dataclass(frozen=True, slots=True)
class ExportProgramMetricsInput:
    """Closed range of days and the target directory."""

    from_day: date
    to_day: date
    out_dir: Path
    force: bool


@dataclass(frozen=True, slots=True)
class ExportedFile:
    """One written file: its name and the number of data rows (lines for the text file)."""

    name: str
    rows: int


class ExportProgramMetrics:
    """Read the stored aggregates of a range and write files ``00`` to ``06``."""

    def __init__(self, ports: ExportProgramMetricsPorts) -> None:
        self._reader = ports.reader
        self._prices = ports.prices
        self._writer = ports.writer
        self._clock = ports.clock

    async def execute(self, command: ExportProgramMetricsInput) -> tuple[ExportedFile, ...]:
        """Validate the range, build the package and hand it to the writer."""
        if command.to_day < command.from_day:
            raise ProgramExportFailed(ProgramExportErrorKind.INVALID_RANGE)
        if (command.to_day - command.from_day).days + 1 > EXPORT_MAX_DAYS:
            raise ProgramExportFailed(ProgramExportErrorKind.RANGE_TOO_LONG)
        first, last = command.from_day, command.to_day
        daily = await self._reader.fetch_daily(first, last)
        scenarios = await self._reader.fetch_scenarios(first, last)
        llm = await self._reader.fetch_llm(first, last)
        load = await self._reader.fetch_load(first, last)
        cohorts = await self._reader.fetch_cohorts(first, last)
        prices = await self._prices.list_prices()
        files: tuple[TextFile | TableFile, ...] = (
            report.definitions_file(first, last, self._clock.now(), prices),
            report.summary_file(first, last, daily, llm, cohorts),
            report.daily_file(daily),
            report.scenarios_file(scenarios),
            report.llm_models_file(llm),
            report.retention_file(cohorts),
            report.load_peaks_file(load, daily),
        )
        await self._writer.write(command.out_dir, files, force=command.force)
        return tuple(
            ExportedFile(
                name=item.name,
                rows=len(item.content.splitlines())
                if isinstance(item, TextFile)
                else len(item.rows),
            )
            for item in files
        )
