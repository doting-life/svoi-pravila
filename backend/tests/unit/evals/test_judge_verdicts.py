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
    ungrounded_counts,
)

_META = GenerationMeta(
    model="judge",
    prompt_version="judge_clean@v1",
    latency_ms=1,
    attempts=1,
    usage=TokenUsage(1, 1, 0),
)
_SOFTER = "Я не пойду, прости. Мне это неприятно"
_FIRMER = "Я не пойду. Блин, ты вечно так"


def _request(softer: str = _SOFTER, firmer: str = _FIRMER) -> JudgeRequest:
    return JudgeRequest(softer=softer, firmer=firmer, deadline_seconds=5.0)


def _observation(
    softer_rude: tuple[str, ...] = (), firmer_rude: tuple[str, ...] = ()
) -> JudgeObservation:
    return JudgeObservation(softer_rude=softer_rude, firmer_rude=firmer_rude, meta=_META)


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
    result = derive_result(_request(), _observation())
    assert (result.softer_clean, result.firmer_clean) == (Verdict.YES, Verdict.YES)


@pytest.mark.unit
def test_clean_is_no_iff_a_rude_quote_of_that_variant_counts() -> None:
    result = derive_result(_request(), _observation(firmer_rude=("ты вечно так",)))
    assert result.softer_clean is Verdict.YES
    assert result.firmer_clean is Verdict.NO
    result = derive_result(_request(), _observation(softer_rude=("Мне это неприятно",)))
    assert result.softer_clean is Verdict.NO
    assert result.firmer_clean is Verdict.YES


@pytest.mark.unit
@pytest.mark.parametrize("quote", ["блин", "не из текста", "...", ""])
def test_a_rude_quote_that_is_not_in_its_variant_does_not_count(quote: str) -> None:
    result = derive_result(_request(), _observation(softer_rude=(quote,)))
    assert result.softer_clean is Verdict.YES


@pytest.mark.unit
def test_a_quote_of_the_other_variant_does_not_count() -> None:
    result = derive_result(_request(), _observation(softer_rude=("Блин, ты вечно так",)))
    assert result.softer_clean is Verdict.YES


@pytest.mark.unit
def test_a_rude_quote_counts_after_normalisation() -> None:
    result = derive_result(_request(), _observation(softer_rude=("ПРОСТИ!",)))
    assert result.softer_clean is Verdict.NO


@pytest.mark.unit
def test_one_counted_quote_among_ungrounded_ones_is_enough() -> None:
    result = derive_result(_request(), _observation(firmer_rude=("нет такого", "Блин")))
    assert result.firmer_clean is Verdict.NO


@pytest.mark.unit
def test_the_derived_result_keeps_the_meta() -> None:
    assert derive_result(_request(), _observation()).meta is _META


@pytest.mark.unit
def test_ungrounded_quotes_are_counted_per_slot() -> None:
    observation = _observation(
        softer_rude=("прости", "нет такого", "..."), firmer_rude=("нет такого", "Блин")
    )
    counts = ungrounded_counts(_request(), observation)
    assert (counts.softer, counts.firmer, counts.total) == (2, 1, 3)


@pytest.mark.unit
def test_a_fully_grounded_observation_has_no_ungrounded_quotes() -> None:
    counts = ungrounded_counts(_request(), _observation(("прости",), ("Блин",)))
    assert counts.total == 0


@pytest.mark.unit
def test_the_evidence_shows_the_quotes_marking_the_ungrounded_ones() -> None:
    observation = _observation(softer_rude=("прости", "нет такого"))
    assert evidence(_request(), observation, "softer") == "прости; ?нет такого"
    assert evidence(_request(), observation, "firmer") == "-"


@pytest.mark.unit
async def test_the_fake_judge_scripts_verdicts_that_derive_back_to_themselves() -> None:
    request = _request()
    for softer, firmer in [
        (Verdict.NO, Verdict.YES),
        (Verdict.YES, Verdict.NO),
        (Verdict.NO, Verdict.NO),
        (Verdict.YES, Verdict.YES),
    ]:
        scripted = judge_result(softer, firmer)
        derived = derive_result(request, await FakeRewriteJudge(scripted).judge(request))
        assert (derived.softer_clean, derived.firmer_clean) == (softer, firmer)
