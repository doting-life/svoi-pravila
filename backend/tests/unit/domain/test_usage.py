"""UsageEvent domain invariants."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from hypothesis import given
from hypothesis import strategies as st

from svoi_pravila.domain.enums import (
    Firmness,
    UsageEventKind,
    UsageOutcome,
    UsageScenario,
    UsageSurface,
)
from svoi_pravila.domain.errors import InvalidTimestampError, InvalidValueError
from svoi_pravila.domain.ids import UsageEventId
from svoi_pravila.domain.usage import UsageEvent

_PSEUDO = "a" * 64
_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _event() -> UsageEvent:
    return UsageEvent(
        id=UsageEventId(UUID(int=1)),
        occurred_at=_NOW,
        user_pseudonym=_PSEUDO,
        scenario=UsageScenario.DECODE,
        surface=UsageSurface.DM,
        outcome=UsageOutcome.OK,
        unavailable_kind=None,
        safety="ok",
        model="GigaChat-2-Pro",
        prompt_version="decode@v1",
        latency_ms=10,
        ttfc_ms=2,
        attempts=1,
        input_tokens=1,
        output_tokens=1,
        billable_tokens=2,
    )


@pytest.mark.unit
def test_usage_event_accepts_valid_record() -> None:
    event = _event()
    assert event.user_pseudonym == _PSEUDO
    assert event.scenario is UsageScenario.DECODE


@pytest.mark.unit
def test_usage_event_rejects_naive_time() -> None:
    with pytest.raises(InvalidTimestampError):
        replace(_event(), occurred_at=datetime(2026, 1, 1))


@pytest.mark.unit
def test_usage_event_rejects_subsecond() -> None:
    with pytest.raises(InvalidValueError, match="truncated"):
        replace(_event(), occurred_at=_NOW + timedelta(microseconds=1))


@pytest.mark.unit
def test_usage_event_rejects_bad_pseudonym() -> None:
    with pytest.raises(InvalidValueError, match="64 lowercase"):
        replace(_event(), user_pseudonym="A" * 64)
    with pytest.raises(InvalidValueError, match="64 lowercase"):
        replace(_event(), user_pseudonym="a" * 63)
    with pytest.raises(InvalidValueError, match="64 lowercase"):
        replace(_event(), user_pseudonym="g" * 64)


@pytest.mark.unit
def test_usage_event_rejects_empty_model_or_prompt() -> None:
    with pytest.raises(InvalidValueError, match="non-empty"):
        replace(_event(), model="")
    with pytest.raises(InvalidValueError, match="non-empty"):
        replace(_event(), prompt_version="")


@pytest.mark.unit
def test_usage_event_rejects_negative_latencies_and_tokens() -> None:
    with pytest.raises(InvalidValueError, match="non-negative"):
        replace(_event(), latency_ms=-1)
    with pytest.raises(InvalidValueError, match="non-negative"):
        replace(_event(), ttfc_ms=-1)
    with pytest.raises(InvalidValueError, match="at least 1"):
        replace(_event(), attempts=0)
    with pytest.raises(InvalidValueError, match="token"):
        replace(_event(), input_tokens=-1)
    with pytest.raises(InvalidValueError, match="token"):
        replace(_event(), output_tokens=-1)
    with pytest.raises(InvalidValueError, match="token"):
        replace(_event(), billable_tokens=-1)


@pytest.mark.unit
def test_usage_event_result_chosen_requires_firmness() -> None:
    event = UsageEvent(
        id=UsageEventId(UUID(int=2)),
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
        variant_firmness=Firmness.GENTLE,
    )
    assert event.event_kind is UsageEventKind.RESULT_CHOSEN
    with pytest.raises(InvalidValueError, match="variant_firmness"):
        replace(event, variant_firmness=None)
    with pytest.raises(InvalidValueError, match="model"):
        replace(event, model="x")
    with pytest.raises(InvalidValueError, match="generation metrics"):
        replace(event, attempts=1)
    with pytest.raises(InvalidValueError, match="outcome"):
        replace(event, outcome=UsageOutcome.REFUSED)
    with pytest.raises(InvalidValueError, match="surface"):
        replace(event, surface=UsageSurface.DM)


@pytest.mark.unit
def test_usage_event_generation_rejects_firmness() -> None:
    with pytest.raises(InvalidValueError, match="variant_firmness"):
        replace(_event(), variant_firmness=Firmness.FIRM)


@pytest.mark.unit
def test_usage_event_screened_crisis_without_model() -> None:
    event = UsageEvent(
        id=UsageEventId(UUID(int=3)),
        occurred_at=_NOW,
        user_pseudonym=_PSEUDO,
        scenario=UsageScenario.DECODE,
        surface=UsageSurface.DM,
        outcome=UsageOutcome.SCREENED,
        unavailable_kind=None,
        safety="crisis",
        model=None,
        prompt_version=None,
        latency_ms=0,
        ttfc_ms=None,
        attempts=0,
        input_tokens=0,
        output_tokens=0,
        billable_tokens=0,
    )
    assert event.outcome is UsageOutcome.SCREENED
    with pytest.raises(InvalidValueError, match="safety=crisis"):
        replace(event, safety="ok")
    with pytest.raises(InvalidValueError, match="model"):
        replace(event, model="x")
    with pytest.raises(InvalidValueError, match="generation metrics"):
        replace(event, attempts=1)


@given(st.text(alphabet="0123456789abcdef", min_size=64, max_size=64))
@pytest.mark.unit
def test_usage_event_hex_pseudonym_round_trip(hex64: str) -> None:
    event = replace(_event(), user_pseudonym=hex64)
    assert event.user_pseudonym == hex64
