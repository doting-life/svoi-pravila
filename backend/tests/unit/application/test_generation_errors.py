"""Application generation error constructors (C0 classifiers)."""

from __future__ import annotations

import pytest

from svoi_pravila.application.errors import (
    GenerationRefusedByProvider,
    GenerationUnavailable,
    InvalidGenerationOutput,
    InvalidOutputReason,
    UnavailableKind,
)
from svoi_pravila.application.ports.generation import TokenUsage


@pytest.mark.unit
def test_invalid_generation_output_carries_reasons() -> None:
    usage = TokenUsage(input=3, output=5, precached=1)
    err = InvalidGenerationOutput(
        (InvalidOutputReason.EMPTY_MESSAGE, InvalidOutputReason.JSON_DECODE),
        usage=usage,
        attempts=2,
    )
    assert err.reasons == (InvalidOutputReason.EMPTY_MESSAGE, InvalidOutputReason.JSON_DECODE)
    assert err.usage == usage
    assert err.usage.billable == 8
    assert err.attempts == 2
    assert str(err) == "invalid generation output"


@pytest.mark.unit
def test_generation_refused_carries_usage_and_attempts() -> None:
    err = GenerationRefusedByProvider(usage=TokenUsage(output=2), attempts=1)
    assert err.usage.output == 2
    assert err.usage.billable == 2
    assert err.attempts == 1


@pytest.mark.unit
def test_invalid_generation_output_attempts_explicit() -> None:
    err = InvalidGenerationOutput(
        (InvalidOutputReason.EMPTY_MESSAGE,),
        usage=TokenUsage(),
        attempts=3,
    )
    assert err.attempts == 3


@pytest.mark.unit
def test_all_invalid_output_reasons_are_c0_tokens() -> None:
    values = {member.value for member in InvalidOutputReason}
    assert "empty_message" in values
    assert "analysis_too_long" in values
    assert "boundary_collision" in values
    assert "stream_separator_missing" not in values
    assert len(values) == len(InvalidOutputReason)


@pytest.mark.unit
def test_invalid_generation_output_rejects_empty_reasons() -> None:
    with pytest.raises(ValueError, match="at least one reason"):
        InvalidGenerationOutput((), usage=TokenUsage(), attempts=0)


@pytest.mark.unit
def test_unavailable_kind_on_error() -> None:
    err = GenerationUnavailable(
        UnavailableKind.RATE_LIMITED,
        usage=TokenUsage(input=1, output=0),
        attempts=1,
    )
    assert err.kind is UnavailableKind.RATE_LIMITED
    assert err.usage.billable == 1
    assert err.attempts == 1
    assert str(err) == "generation unavailable"
    assert {k.value for k in UnavailableKind} == {
        "timeout",
        "rate_limited",
        "auth",
        "server",
        "network",
    }


@pytest.mark.unit
def test_token_usage_billable_is_derived() -> None:
    usage = TokenUsage(input=1, output=4, precached=37)
    assert usage.billable == 5
    combined = usage + TokenUsage(input=2, output=3, precached=1)
    assert combined.input == 3
    assert combined.output == 7
    assert combined.precached == 38
    assert combined.billable == 10
