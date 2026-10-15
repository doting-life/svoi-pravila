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
def test_a_request_holds_one_text_and_the_deadline_only() -> None:
    assert [field.name for field in dataclasses.fields(JudgeRequest)] == [
        "text",
        "deadline_seconds",
    ]


@pytest.mark.unit
def test_the_result_holds_the_verdict_and_meta_never_text() -> None:
    assert [field.name for field in dataclasses.fields(JudgeResult)] == ["clean", "meta"]
    assert judge_result(Verdict.NO).clean is Verdict.NO


@pytest.mark.unit
def test_the_observation_holds_the_rude_quotes_of_the_text() -> None:
    assert [field.name for field in dataclasses.fields(JudgeObservation)] == ["rude", "meta"]
