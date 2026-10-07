"""MetricsUsageEventSink maps every outcome onto the expected series."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest
from prometheus_client import REGISTRY
from tests.fakes.usage_sink import RecordingUsageEventSink

from svoi_pravila.adapters.persistence.metrics_usage_sink import MetricsUsageEventSink
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

_PSEUDO = "b" * 64
_NOW = datetime(2026, 3, 1, tzinfo=UTC)


def _sample(metric_name: str, labels: dict[str, str]) -> float:
    for family in REGISTRY.collect():
        for sample in family.samples:
            if sample.name != metric_name:
                continue
            if all(sample.labels.get(key) == value for key, value in labels.items()):
                return float(sample.value)
    return 0.0


def _counter(name: str, **labels: str) -> float:
    return _sample(name, labels)


def _hist_sum(name: str, **labels: str) -> float:
    return _sample(f"{name}_sum", labels)


def _generation(
    *,
    outcome: UsageOutcome,
    scenario: UsageScenario = UsageScenario.DECODE,
    surface: UsageSurface = UsageSurface.MINIAPP,
    ttfc_ms: int | None = 12,
    limit_kind: LimitKind | None = None,
    model: str | None = "GigaChat-2-Pro",
    prompt_version: str | None = "decode@v3",
    input_tokens: int = 3,
    output_tokens: int = 5,
    billable_tokens: int = 8,
    latency_ms: int = 100,
    attempts: int = 1,
    safety: str | None = "ok",
) -> UsageEvent:
    if outcome is UsageOutcome.SCREENED:
        return UsageEvent(
            id=UsageEventId(UUID(int=10)),
            occurred_at=_NOW,
            user_pseudonym=_PSEUDO,
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
    if outcome is UsageOutcome.LIMITED:
        return UsageEvent(
            id=UsageEventId(UUID(int=11)),
            occurred_at=_NOW,
            user_pseudonym=_PSEUDO,
            scenario=scenario,
            surface=surface,
            outcome=outcome,
            unavailable_kind=None,
            safety=None,
            model=None,
            prompt_version=None,
            latency_ms=latency_ms,
            ttfc_ms=None,
            attempts=0,
            input_tokens=0,
            output_tokens=0,
            billable_tokens=0,
            limit_kind=limit_kind or LimitKind.USER_QUOTA,
        )
    return UsageEvent(
        id=UsageEventId(UUID(int=12)),
        occurred_at=_NOW,
        user_pseudonym=_PSEUDO,
        scenario=scenario,
        surface=surface,
        outcome=outcome,
        unavailable_kind=None if outcome is not UsageOutcome.UNAVAILABLE else "timeout",
        safety=safety,
        model=model,
        prompt_version=prompt_version,
        latency_ms=latency_ms,
        ttfc_ms=ttfc_ms,
        attempts=attempts,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        billable_tokens=billable_tokens,
    )


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", list(UsageOutcome))
async def test_sink_maps_every_outcome(outcome: UsageOutcome) -> None:
    inner = RecordingUsageEventSink()
    sink = MetricsUsageEventSink(inner)
    event = _generation(
        outcome=outcome,
        ttfc_ms=None
        if outcome
        in {
            UsageOutcome.SCREENED,
            UsageOutcome.LIMITED,
        }
        else 12,
    )
    usage_labels = {
        "scenario": event.scenario.value,
        "surface": event.surface.value,
        "outcome": event.outcome.value,
        "limit_kind": (
            event.limit_kind.value
            if event.outcome is UsageOutcome.LIMITED and event.limit_kind is not None
            else "none"
        ),
    }
    before = _counter("sp_usage_events_total", **usage_labels)
    await sink.record(event)
    assert len(inner.events) == 1
    assert _counter("sp_usage_events_total", **usage_labels) == before + 1.0
    duration_labels = {
        "scenario": event.scenario.value,
        "surface": event.surface.value,
        "outcome": event.outcome.value,
        "model": "none" if event.model is None else event.model,
        "prompt_version": "none" if event.prompt_version is None else event.prompt_version,
    }
    assert (
        _hist_sum("sp_llm_request_duration_seconds", **duration_labels) >= event.latency_ms / 1000.0
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_ttfc_only_when_present_and_tokens_on_generation() -> None:
    inner = RecordingUsageEventSink()
    sink = MetricsUsageEventSink(inner)
    with_ttfc = _generation(outcome=UsageOutcome.OK, ttfc_ms=40)
    without = _generation(
        outcome=UsageOutcome.OK,
        scenario=UsageScenario.SOFTEN,
        surface=UsageSurface.INLINE,
        ttfc_ms=None,
        model="GigaChat-3-Lightning",
        prompt_version="soften@v3",
    )
    ttfc_before = _hist_sum("sp_llm_ttfc_seconds", scenario="decode", surface="miniapp")
    await sink.record(with_ttfc)
    assert _hist_sum("sp_llm_ttfc_seconds", scenario="decode", surface="miniapp") >= (
        ttfc_before + 0.04
    )
    tokens_before = _counter("sp_llm_tokens_total", scenario="soften", kind="billable")
    await sink.record(without)
    assert _counter("sp_llm_tokens_total", scenario="soften", kind="billable") == (
        tokens_before + 8.0
    )
    assert _hist_sum("sp_llm_ttfc_seconds", scenario="soften", surface="inline") >= 0.0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_result_chosen_counts_usage_only() -> None:
    inner = RecordingUsageEventSink()
    sink = MetricsUsageEventSink(inner)
    event = UsageEvent(
        id=UsageEventId(UUID(int=20)),
        occurred_at=_NOW,
        user_pseudonym=_PSEUDO,
        scenario=UsageScenario.SOFTEN,
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
        variant_firmness=Firmness.BALANCED,
    )
    before = _counter(
        "sp_usage_events_total",
        scenario="soften",
        surface="inline",
        outcome="ok",
        limit_kind="none",
    )
    await sink.record(event)
    assert (
        _counter(
            "sp_usage_events_total",
            scenario="soften",
            surface="inline",
            outcome="ok",
            limit_kind="none",
        )
        == before + 1.0
    )
