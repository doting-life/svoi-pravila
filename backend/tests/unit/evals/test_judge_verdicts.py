"""Clean verdicts derived from the judge's observations: each grounding rule tested both ways."""

from __future__ import annotations

import pytest
from tests.fakes.judge import FakeRewriteJudge, judge_result

from svoi_pravila.application.ports.generation import GenerationMeta, TokenUsage
from svoi_pravila.application.ports.rewrite_judge import JudgeObservation, JudgeRequest, Verdict
from svoi_pravila.evals.judge_verdicts import (
    derive_result,
    evidence,
    normalise,
    ungrounded,
)

_META = GenerationMeta(
    model="judge",
    prompt_version="judge_clean@v2",
    latency_ms=1,
    attempts=1,
    usage=TokenUsage(1, 1, 0),
)
_TEXT = "Я не пойду. Блин, ты вечно так"


def _request(text: str = _TEXT) -> JudgeRequest:
    return JudgeRequest(text=text, deadline_seconds=5.0)


def _observation(*rude: str) -> JudgeObservation:
    return JudgeObservation(rude=rude, meta=_META)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Мне, нужно: ВРЕМЯ!", "мне нужно время"),
        ("Всё  ещё\tтут", "все еще тут"),
        ("  ...  ", ""),
        ("х*й", "х й"),
        ("a_b", "a_b"),
    ],
)
def test_normalise_folds_case_yo_punctuation_and_spaces(text: str, expected: str) -> None:
    assert normalise(text) == expected


@pytest.mark.unit
def test_clean_is_yes_without_rude_quotes() -> None:
    assert derive_result(_request(), _observation()).clean is Verdict.YES


@pytest.mark.unit
def test_clean_is_no_iff_a_rude_quote_of_the_text_counts() -> None:
    assert derive_result(_request(), _observation("ты вечно так")).clean is Verdict.NO


@pytest.mark.unit
@pytest.mark.parametrize("quote", ["прости", "не из текста", "...", ""])
def test_a_rude_quote_that_is_not_in_the_text_does_not_count(quote: str) -> None:
    assert derive_result(_request(), _observation(quote)).clean is Verdict.YES


@pytest.mark.unit
def test_a_rude_quote_counts_after_normalisation() -> None:
    assert derive_result(_request(), _observation("БЛИН!")).clean is Verdict.NO


@pytest.mark.unit
def test_one_counted_quote_among_ungrounded_ones_is_enough() -> None:
    assert derive_result(_request(), _observation("нет такого", "Блин")).clean is Verdict.NO


@pytest.mark.unit
def test_the_derived_result_keeps_the_meta() -> None:
    assert derive_result(_request(), _observation()).meta is _META


@pytest.mark.unit
def test_ungrounded_quotes_are_counted() -> None:
    assert ungrounded(_request(), _observation("прости", "нет такого", "...", "ты вечно")) == 3


@pytest.mark.unit
def test_a_fully_grounded_observation_has_no_ungrounded_quotes() -> None:
    assert ungrounded(_request(), _observation("Блин", "ты вечно")) == 0


@pytest.mark.unit
def test_the_evidence_shows_the_quotes_marking_the_ungrounded_ones() -> None:
    assert evidence(_request(), _observation("Блин", "нет такого")) == "Блин; ?нет такого"
    assert evidence(_request(), _observation()) == "-"


@pytest.mark.unit
async def test_the_fake_judge_scripts_verdicts_that_derive_back_to_themselves() -> None:
    request = _request()
    for clean in (Verdict.NO, Verdict.YES):
        derived = derive_result(request, await FakeRewriteJudge(judge_result(clean)).judge(request))
        assert derived.clean is clean
