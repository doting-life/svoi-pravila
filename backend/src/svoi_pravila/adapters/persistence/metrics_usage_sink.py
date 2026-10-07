"""UsageEventSink decorator that updates Prometheus from persisted events."""

from __future__ import annotations

from typing import Protocol

from svoi_pravila.domain.enums import UsageEventKind, UsageOutcome
from svoi_pravila.domain.usage import UsageEvent
from svoi_pravila.observability.metrics import families
from svoi_pravila.observability.metrics.labels import (
    limit_kind_label,
    model_label,
    outcome_label,
    prompt_version_label,
    scenario_label,
    surface_label,
)


class _UsageEventSink(Protocol):
    async def record(self, event: UsageEvent) -> None:
        """Persist one usage event."""
        ...


class MetricsUsageEventSink:
    """Forward ``record`` to an inner sink, then update LLM / usage metrics."""

    def __init__(self, inner: _UsageEventSink) -> None:
        self._inner = inner

    async def record(self, event: UsageEvent) -> None:
        """Persist via the inner sink, then observe metrics from the same event."""
        await self._inner.record(event)
        _observe(event)


def _observe(event: UsageEvent) -> None:
    scenario = scenario_label(event.scenario)
    surface = surface_label(event.surface)
    outcome = outcome_label(event.outcome)
    limit_kind = limit_kind_label(event.limit_kind, outcome=event.outcome)
    families.USAGE_EVENTS.labels(
        scenario=scenario,
        surface=surface,
        outcome=outcome,
        limit_kind=limit_kind,
    ).inc()
    if event.event_kind is not UsageEventKind.GENERATION:
        return
    families.LLM_REQUEST_DURATION.labels(
        scenario=scenario,
        surface=surface,
        outcome=outcome,
        model=model_label(event.model),
        prompt_version=prompt_version_label(event.prompt_version),
    ).observe(event.latency_ms / 1000.0)
    if event.ttfc_ms is not None:
        families.LLM_TTFC.labels(scenario=scenario, surface=surface).observe(event.ttfc_ms / 1000.0)
    if event.outcome is UsageOutcome.LIMITED or event.outcome is UsageOutcome.SCREENED:
        return
    families.LLM_TOKENS.labels(scenario=scenario, kind="input").inc(event.input_tokens)
    families.LLM_TOKENS.labels(scenario=scenario, kind="output").inc(event.output_tokens)
    families.LLM_TOKENS.labels(scenario=scenario, kind="billable").inc(event.billable_tokens)
