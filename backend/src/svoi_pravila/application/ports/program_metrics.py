"""Read port for stored program-metrics aggregates (no usage events, no pseudonyms)."""

from __future__ import annotations

from datetime import date
from typing import Protocol

from svoi_pravila.domain.analytics import (
    CohortTotals,
    DailyTotals,
    LlmModelTotals,
    LoadPeaks,
    ScenarioTotals,
)


class ProgramMetricsReader(Protocol):
    """Stored daily aggregates for a closed range of calendar days."""

    async def fetch_daily(self, from_day: date, to_day: date) -> tuple[DailyTotals, ...]:
        """``analytics_daily`` rows ordered by day."""
        ...

    async def fetch_scenarios(self, from_day: date, to_day: date) -> tuple[ScenarioTotals, ...]:
        """``analytics_daily_scenario`` rows ordered by day, scenario, surface."""
        ...

    async def fetch_llm(self, from_day: date, to_day: date) -> tuple[LlmModelTotals, ...]:
        """``analytics_daily_llm`` rows ordered by day, scenario, surface, model."""
        ...

    async def fetch_load(self, from_day: date, to_day: date) -> tuple[LoadPeaks, ...]:
        """``analytics_daily_load`` rows ordered by day."""
        ...

    async def fetch_cohorts(self, from_day: date, to_day: date) -> tuple[CohortTotals, ...]:
        """``analytics_cohorts`` rows whose cohort day is in the range, ordered by day."""
        ...
