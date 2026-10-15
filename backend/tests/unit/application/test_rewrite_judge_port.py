"""The judge port: the clean verdict has two values and results carry no text."""

from __future__ import annotations

import dataclasses

import pytest

from svoi_pravila.application.ports.rewrite_judge import (
    JudgeObservation,
    JudgeRequest,
    JudgeResult,
    Verdict,
)
from tests.fakes.judge import judge_result


@pytest.mark.unit
def test_the_clean_verdict_is_yes_or_no() -> None:
    assert {verdict.value for verdict in Verdict} == {"yes", "no"}


@pytest.mark.unit
def test_a_request_holds_the_two_variants_and_the_deadline_only() -> None:
    assert [field.name for field in dataclasses.fields(JudgeRequest)] == [
        "softer",
        "firmer",
        "deadline_seconds",
    ]


@pytest.mark.unit
def test_the_result_holds_verdicts_and_meta_never_text() -> None:
    assert [field.name for field in dataclasses.fields(JudgeResult)] == [
        "softer_clean",
        "firmer_clean",
        "meta",
    ]
    result = judge_result(Verdict.NO, Verdict.YES)
    assert (result.softer_clean, result.firmer_clean) == (Verdict.NO, Verdict.YES)


@pytest.mark.unit
def test_the_observation_holds_the_rude_quotes_of_each_variant() -> None:
    assert [field.name for field in dataclasses.fields(JudgeObservation)] == [
        "softer_rude",
        "firmer_rude",
        "meta",
    ]
