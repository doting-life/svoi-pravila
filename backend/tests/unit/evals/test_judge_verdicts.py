"""Verdicts derived from the judge's observations: one test per row of the table, both ways."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import pytest
from tests.fakes.judge import FakeRewriteJudge, judge_result, slot

from svoi_pravila.application.ports.generation import GenerationMeta, RuleContext, TokenUsage
from svoi_pravila.application.ports.rewrite_judge import (
    GoalBreach,
    JudgeObservation,
    JudgeRequest,
    PairObservation,
    Position,
    RuleCheck,
    SlotObservation,
    SlotVerdicts,
    Verdict,
    Warmer,
)
from svoi_pravila.domain.enums import RewriteGoal, RuleCategory
from svoi_pravila.evals.judge_verdicts import (
    GOAL_BREACHES,
    derive_result,
    evidence,
    normalise,
    ungrounded_counts,
    words_differ,
)

_META = GenerationMeta(
    model="judge",
    prompt_version="judge_rewrite@v3",
    latency_ms=1,
    attempts=1,
    usage=TokenUsage(1, 1, 0),
)
_RULE = RuleContext(
    category=RuleCategory.HOW_TO_ASK,
    text="заканчивать вопросом",
    effective_since=datetime(2026, 1, 1, tzinfo=UTC),
)
_GOOD_SLOT = SlotObservation(
    rude=(),
    added=(),
    position=Position.SAME,
    goal_sentence="Я не пойду",
    goal_breach=GoalBreach.NONE,
    rule_evidence=("Прошу тебя",),
    rule_check=RuleCheck.FOLLOWED,
)
_GOOD_PAIR = PairObservation(firmer_softening=(), firmer_pressure=(), warmer=Warmer.SOFTER)
_SOFTER = "Я не пойду, прости. Мне это неприятно. Прошу тебя"
_FIRMER = "Я не пойду. Прошу тебя. Блин, ты вечно так"


def _request(
    *,
    goal: RewriteGoal | None = None,
    rules: tuple[RuleContext, ...] = (),
    draft: str = "Я не пойду, ты опять опоздал",
    softer: str = _SOFTER,
    firmer: str = _FIRMER,
) -> JudgeRequest:
    return JudgeRequest(
        draft=draft,
        goal=goal,
        rules=rules,
        softer=softer,
        firmer=firmer,
        deadline_seconds=5.0,
    )


def _observation(
    softer: SlotObservation = _GOOD_SLOT,
    firmer: SlotObservation = _GOOD_SLOT,
    pair: PairObservation = _GOOD_PAIR,
) -> JudgeObservation:
    return JudgeObservation(softer=softer, firmer=firmer, pair=pair, meta=_META)


def _softer(request: JudgeRequest, seen: SlotObservation = _GOOD_SLOT) -> SlotVerdicts:
    return derive_result(request, _observation(softer=seen)).softer


def _with(**changes: Any) -> SlotObservation:
    return replace(_GOOD_SLOT, **changes)


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


# clean


@pytest.mark.unit
def test_clean_is_yes_iff_no_rude_quote_counts() -> None:
    request = _request()
    assert _softer(request).clean is Verdict.YES
    assert _softer(request, _with(rude=("мне это неприятно",))).clean is Verdict.NO


@pytest.mark.unit
@pytest.mark.parametrize("quote", ["блин", "не из текста", "...", ""])
def test_a_rude_quote_that_is_not_in_its_variant_does_not_count(quote: str) -> None:
    assert _softer(_request(), _with(rude=(quote,))).clean is Verdict.YES


@pytest.mark.unit
def test_a_rude_quote_counts_after_normalisation() -> None:
    assert _softer(_request(), _with(rude=("ПРОСТИ!",))).clean is Verdict.NO


# invented


@pytest.mark.unit
def test_invented_is_yes_iff_an_added_quote_counts() -> None:
    request = _request()
    assert _softer(request).invented is Verdict.NO
    assert _softer(request, _with(added=("мне это неприятно",))).invented is Verdict.YES


@pytest.mark.unit
@pytest.mark.parametrize("quote", ["не из текста", "...", ""])
def test_an_added_quote_that_is_not_in_its_variant_does_not_count(quote: str) -> None:
    assert _softer(_request(), _with(added=(quote,))).invented is Verdict.NO


@pytest.mark.unit
def test_an_added_quote_that_occurs_in_the_draft_does_not_count() -> None:
    request = _request(softer="Ты опять опоздал, прости")
    assert _softer(request, _with(added=("Ты опять опоздал",))).invented is Verdict.NO
    assert _softer(request, _with(added=("прости",))).invented is Verdict.YES


@pytest.mark.unit
@pytest.mark.parametrize(
    ("rude", "added"),
    [
        ("мне это неприятно", "мне это неприятно"),
        ("мне это неприятно", "это"),
        ("неприятно", "мне это неприятно"),
    ],
    ids=["same", "added-inside-rude", "rude-inside-added"],
)
def test_an_added_quote_overlapping_a_counted_rude_quote_does_not_count(
    rude: str, added: str
) -> None:
    seen = _with(rude=(rude,), added=(added,))
    assert _softer(_request(), seen).invented is Verdict.NO
    assert _softer(_request(), seen).clean is Verdict.NO


@pytest.mark.unit
def test_an_added_quote_overlapping_an_ungrounded_rude_quote_still_counts() -> None:
    seen = _with(rude=("не из текста",), added=("прости",))
    assert _softer(_request(), seen).invented is Verdict.YES


@pytest.mark.unit
def test_an_added_quote_apart_from_the_rude_quote_counts() -> None:
    seen = _with(rude=("прости",), added=("мне это неприятно",))
    assert _softer(_request(), seen).invented is Verdict.YES


# meaning_kept


@pytest.mark.unit
@pytest.mark.parametrize("position", list(Position))
def test_meaning_kept_is_yes_iff_the_position_is_the_same(position: Position) -> None:
    expected = Verdict.YES if position is Position.SAME else Verdict.NO
    assert _softer(_request(), _with(position=position)).meaning_kept is expected


# goal_done


@pytest.mark.unit
@pytest.mark.parametrize("sentence", ["", "   ", "не из текста", "..."])
def test_goal_done_is_no_without_a_goal_sentence_that_is_in_the_variant(sentence: str) -> None:
    request = _request(goal=RewriteGoal.DECLINE)
    assert _softer(request, _with(goal_sentence=sentence)).goal_done is Verdict.NO


@pytest.mark.unit
def test_goal_done_is_yes_with_a_grounded_sentence_and_no_breach() -> None:
    assert _softer(_request(goal=RewriteGoal.DECLINE)).goal_done is Verdict.YES


@pytest.mark.unit
@pytest.mark.parametrize("goal", list(RewriteGoal))
@pytest.mark.parametrize("breach", list(GoalBreach))
def test_goal_done_counts_only_the_breaches_of_that_goal(
    goal: RewriteGoal, breach: GoalBreach
) -> None:
    expected = Verdict.NO if breach in GOAL_BREACHES[goal] else Verdict.YES
    assert _softer(_request(goal=goal), _with(goal_breach=breach)).goal_done is expected


@pytest.mark.unit
def test_the_breaches_per_goal_are_the_cto_table() -> None:
    assert {
        RewriteGoal.DECLINE: {GoalBreach.HEDGED, GoalBreach.CONDITIONAL},
        RewriteGoal.SET_BOUNDARY: {GoalBreach.WISH_OR_QUESTION},
        RewriteGoal.ADMIT_FAULT: {GoalBreach.EXCUSE, GoalBreach.SELF_BLAME},
        RewriteGoal.RECONNECT: {
            GoalBreach.REPROACH,
            GoalBreach.PROHIBITION,
            GoalBreach.ULTIMATUM,
        },
    } == GOAL_BREACHES


@pytest.mark.unit
@pytest.mark.parametrize("sentence", ["", "Я не пойду"])
@pytest.mark.parametrize("breach", list(GoalBreach))
def test_goal_done_is_na_without_a_goal_whatever_the_judge_answers(
    sentence: str, breach: GoalBreach
) -> None:
    seen = _with(goal_sentence=sentence, goal_breach=breach)
    assert _softer(_request(goal=None), seen).goal_done is Verdict.NA


# rule_done


@pytest.mark.unit
def test_rule_done_is_yes_for_followed_with_a_counted_evidence_quote() -> None:
    assert _softer(_request(rules=(_RULE,))).rule_done is Verdict.YES


@pytest.mark.unit
@pytest.mark.parametrize("evidence_quotes", [(), ("не из текста",), ("...",), ("",)])
def test_rule_done_is_no_for_followed_without_a_counted_evidence_quote(
    evidence_quotes: tuple[str, ...],
) -> None:
    seen = _with(rule_evidence=evidence_quotes)
    assert _softer(_request(rules=(_RULE,)), seen).rule_done is Verdict.NO


@pytest.mark.unit
def test_rule_done_is_no_for_broken_even_with_evidence() -> None:
    seen = _with(rule_check=RuleCheck.BROKEN)
    assert _softer(_request(rules=(_RULE,)), seen).rule_done is Verdict.NO


@pytest.mark.unit
def test_rule_done_is_na_for_not_applicable() -> None:
    seen = _with(rule_check=RuleCheck.NOT_APPLICABLE)
    assert _softer(_request(rules=(_RULE,)), seen).rule_done is Verdict.NA


@pytest.mark.unit
@pytest.mark.parametrize("check", list(RuleCheck))
def test_rule_done_is_na_without_rules_whatever_the_judge_answers(check: RuleCheck) -> None:
    assert _softer(_request(rules=()), _with(rule_check=check)).rule_done is Verdict.NA


# ladder_ok


def _ladder(pair: PairObservation = _GOOD_PAIR, request: JudgeRequest | None = None) -> Verdict:
    return derive_result(request or _request(), _observation(pair=pair)).ladder_ok


@pytest.mark.unit
def test_ladder_ok_is_yes_for_a_plain_firmer_a_warmer_softer_and_different_words() -> None:
    assert _ladder() is Verdict.YES


@pytest.mark.unit
@pytest.mark.parametrize(
    "pair",
    [
        replace(_GOOD_PAIR, firmer_softening=("Прошу тебя",)),
        replace(_GOOD_PAIR, firmer_pressure=("ты вечно так",)),
        replace(_GOOD_PAIR, warmer=Warmer.FIRMER),
        replace(_GOOD_PAIR, warmer=Warmer.EQUAL),
    ],
    ids=["softening", "pressure", "firmer-warmer", "equal-warmth"],
)
def test_ladder_ok_is_no_when_any_condition_fails(pair: PairObservation) -> None:
    assert _ladder(pair) is Verdict.NO


@pytest.mark.unit
@pytest.mark.parametrize(
    "pair",
    [
        replace(_GOOD_PAIR, firmer_softening=("прости",)),
        replace(_GOOD_PAIR, firmer_pressure=("не из текста",)),
        replace(_GOOD_PAIR, firmer_softening=("...",), firmer_pressure=("",)),
    ],
    ids=["quote-only-in-softer", "not-in-any-text", "no-words"],
)
def test_ladder_quotes_count_only_when_they_are_in_firmer(pair: PairObservation) -> None:
    assert _ladder(pair) is Verdict.YES


@pytest.mark.unit
def test_ladder_ok_is_no_for_the_same_words_whatever_the_judge_answers() -> None:
    assert _ladder(request=_request(softer="Я не пойду", firmer="я, не пойду!")) is Verdict.NO


@pytest.mark.unit
@pytest.mark.parametrize(
    ("softer", "firmer", "differ"),
    [
        ("Мне нужно время.", "мне нужно время", False),
        ("Мне, нужно: время!", "Мне нужно время", False),
        ("Время нужно мне", "Мне нужно время", False),
        ("Мне нужно время", "Мне нужно времени", True),
        ("Мне нужно время", "Мне нужно время время", True),
        ("Всё это ещё", "все это еще", False),
    ],
    ids=["case-punctuation", "punctuation-only", "reordered", "one-word", "repeated", "yo"],
)
def test_the_word_comparison(softer: str, firmer: str, differ: bool) -> None:  # noqa: FBT001
    assert words_differ(softer, firmer) is differ


# the result and the report


@pytest.mark.unit
def test_the_derived_result_keeps_the_meta_and_every_slot() -> None:
    observation = _observation(firmer=_with(rude=("Блин",)))
    result = derive_result(_request(), observation)
    assert result.meta is _META
    assert result.firmer.clean is Verdict.NO
    assert result.softer.clean is Verdict.YES


@pytest.mark.unit
def test_ungrounded_quotes_are_counted_per_field() -> None:
    observation = _observation(
        softer=_with(
            rude=("прости", "нет такого"),
            added=("нет такого", "нет такого тоже"),
            goal_sentence="нет такого",
            rule_evidence=("Прошу тебя", "нет такого"),
        ),
        firmer=_with(goal_sentence="  ", rude=("...",)),
        pair=PairObservation(("прости", "Прошу тебя"), ("нет такого",), Warmer.SOFTER),
    )
    assert ungrounded_counts(_request(), observation) == {
        "rude": 2,
        "added": 2,
        "goal_sentence": 1,
        "rule_evidence": 1,
        "firmer_softening": 1,
        "firmer_pressure": 1,
    }


@pytest.mark.unit
def test_a_fully_grounded_observation_has_no_ungrounded_quotes() -> None:
    counts = ungrounded_counts(_request(), _observation())
    assert set(counts.values()) == {0}


@pytest.mark.unit
def test_the_evidence_shows_the_quotes_marking_the_ungrounded_ones() -> None:
    request = _request()
    observation = _observation(
        softer=_with(
            rude=("прости", "нет такого"),
            added=("Мне это неприятно",),
            goal_breach=GoalBreach.HEDGED,
            goal_sentence="нет такого",
        ),
        pair=PairObservation(("Прошу тебя",), (), Warmer.EQUAL),
    )
    assert evidence(request, observation, "softer", "clean") == "прости; ?нет такого"
    assert evidence(request, observation, "firmer", "clean") == "-"
    assert evidence(request, observation, "softer", "invented") == "Мне это неприятно"
    assert evidence(request, observation, "softer", "meaning_kept") == "same"
    assert evidence(request, observation, "softer", "goal_done") == "?нет такого [hedged]"
    assert evidence(request, observation, "firmer", "goal_done") == "Я не пойду [none]"
    assert evidence(request, observation, "softer", "rule_done") == "followed: Прошу тебя"
    assert evidence(request, observation, None, "ladder_ok") == (
        "softening: Прошу тебя; pressure: -; warmer: equal"
    )


@pytest.mark.unit
async def test_the_fake_judge_scripts_verdicts_that_derive_back_to_themselves() -> None:
    request = _request(goal=RewriteGoal.DECLINE, rules=(_RULE,))
    scripted = judge_result(
        softer=slot(
            clean=Verdict.NO,
            goal_done=Verdict.NO,
            rule_done=Verdict.NO,
        ),
        firmer=slot(
            meaning_kept=Verdict.NO,
            invented=Verdict.YES,
            goal_done=Verdict.YES,
            rule_done=Verdict.YES,
        ),
        ladder_ok=Verdict.YES,
    )
    observation = await FakeRewriteJudge(scripted).judge(request)
    derived = derive_result(request, observation)
    assert (derived.softer, derived.firmer, derived.ladder_ok) == (
        scripted.softer,
        scripted.firmer,
        scripted.ladder_ok,
    )
