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
    words_differ,
)

_META = GenerationMeta(
    model="judge",
    prompt_version="judge_rewrite@v2",
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
    goal_sentence="Я не пойду.",
    position=Position.SAME,
    goal_breach=GoalBreach.NONE,
    rule_check=RuleCheck.FOLLOWED,
)
_GOOD_PAIR = PairObservation(firmer_softening=(), firmer_pressure=(), warmer=Warmer.SOFTER)


def _request(
    *,
    goal: RewriteGoal | None = None,
    rules: tuple[RuleContext, ...] = (),
    softer: str = "Мне нужно время подумать",
    firmer: str = "Мне нужно время",
) -> JudgeRequest:
    return JudgeRequest(
        draft="Дай мне время",
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
def test_clean_is_yes_iff_rude_is_empty() -> None:
    assert _softer(_request()).clean is Verdict.YES
    assert _softer(_request(), _with(rude=("блин",))).clean is Verdict.NO


@pytest.mark.unit
def test_invented_is_yes_iff_added_is_not_empty() -> None:
    assert _softer(_request()).invented is Verdict.NO
    assert _softer(_request(), _with(added=("иначе уйду",))).invented is Verdict.YES


@pytest.mark.unit
@pytest.mark.parametrize("position", list(Position))
def test_meaning_kept_is_yes_iff_the_position_is_the_same(position: Position) -> None:
    expected = Verdict.YES if position is Position.SAME else Verdict.NO
    assert _softer(_request(), _with(position=position)).meaning_kept is expected


@pytest.mark.unit
@pytest.mark.parametrize("goal_sentence", ["", "   "])
def test_goal_done_is_no_without_a_goal_sentence(goal_sentence: str) -> None:
    request = _request(goal=RewriteGoal.DECLINE)
    assert _softer(request, _with(goal_sentence=goal_sentence)).goal_done is Verdict.NO


@pytest.mark.unit
def test_goal_done_is_yes_with_a_sentence_and_no_breach() -> None:
    request = _request(goal=RewriteGoal.DECLINE)
    assert _softer(request).goal_done is Verdict.YES


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
@pytest.mark.parametrize("sentence", ["", "Я не пойду."])
@pytest.mark.parametrize("breach", list(GoalBreach))
def test_goal_done_is_na_without_a_goal_whatever_the_judge_answers(
    sentence: str, breach: GoalBreach
) -> None:
    assert (
        _softer(_request(goal=None), _with(goal_sentence=sentence, goal_breach=breach)).goal_done
        is Verdict.NA
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("check", "expected"),
    [
        (RuleCheck.FOLLOWED, Verdict.YES),
        (RuleCheck.BROKEN, Verdict.NO),
        (RuleCheck.NOT_APPLICABLE, Verdict.NA),
    ],
)
def test_rule_done_maps_the_rule_check_when_the_request_has_rules(
    check: RuleCheck, expected: Verdict
) -> None:
    assert _softer(_request(rules=(_RULE,)), _with(rule_check=check)).rule_done is expected


@pytest.mark.unit
@pytest.mark.parametrize("check", list(RuleCheck))
def test_rule_done_is_na_without_rules_whatever_the_judge_answers(check: RuleCheck) -> None:
    assert _softer(_request(rules=()), _with(rule_check=check)).rule_done is Verdict.NA


def _ladder(pair: PairObservation = _GOOD_PAIR, request: JudgeRequest | None = None) -> Verdict:
    return derive_result(request or _request(), _observation(pair=pair)).ladder_ok


@pytest.mark.unit
def test_ladder_ok_is_yes_for_a_plain_firmer_a_warmer_softer_and_different_words() -> None:
    assert _ladder() is Verdict.YES


@pytest.mark.unit
@pytest.mark.parametrize(
    "pair",
    [
        replace(_GOOD_PAIR, firmer_softening=("если можно",)),
        replace(_GOOD_PAIR, firmer_pressure=("или уйду",)),
        replace(_GOOD_PAIR, warmer=Warmer.FIRMER),
        replace(_GOOD_PAIR, warmer=Warmer.EQUAL),
    ],
    ids=["softening", "pressure", "firmer-warmer", "equal-warmth"],
)
def test_ladder_ok_is_no_when_any_condition_fails(pair: PairObservation) -> None:
    assert _ladder(pair) is Verdict.NO


@pytest.mark.unit
def test_ladder_ok_is_no_for_the_same_words_whatever_the_judge_answers() -> None:
    assert (
        _ladder(request=_request(softer="Мне нужно время", firmer="Мне нужно время")) is Verdict.NO
    )


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


@pytest.mark.unit
def test_the_derived_result_keeps_the_meta_and_every_slot() -> None:
    observation = _observation(softer=_with(rude=("блин",)))
    result = derive_result(_request(), observation)
    assert result.meta is _META
    assert result.softer.clean is Verdict.NO
    assert result.firmer.clean is Verdict.YES


@pytest.mark.unit
def test_the_evidence_shows_the_quotes_or_the_enum() -> None:
    observation = _observation(
        softer=replace(
            _GOOD_SLOT,
            rude=("блин", "чёрт"),
            added=("иначе уйду",),
            goal_breach=GoalBreach.HEDGED,
        ),
        pair=PairObservation(("если можно",), (), Warmer.EQUAL),
    )
    assert evidence(observation, "softer", "clean") == "блин; чёрт"
    assert evidence(observation, "firmer", "clean") == "-"
    assert evidence(observation, "softer", "invented") == "иначе уйду"
    assert evidence(observation, "softer", "meaning_kept") == "same"
    assert evidence(observation, "softer", "goal_done") == "Я не пойду. [hedged]"
    assert evidence(observation, "softer", "rule_done") == "followed"
    assert evidence(observation, None, "ladder_ok") == (
        "softening: если можно; pressure: -; warmer: equal"
    )


@pytest.mark.unit
async def test_the_fake_judge_scripts_verdicts_that_derive_back_to_themselves() -> None:
    request = _request(goal=RewriteGoal.DECLINE, rules=(_RULE,))
    scripted = judge_result(
        softer=slot(
            clean=Verdict.NO,
            invented=Verdict.YES,
            goal_done=Verdict.NO,
            rule_done=Verdict.NO,
        ),
        firmer=slot(meaning_kept=Verdict.NO, goal_done=Verdict.YES, rule_done=Verdict.YES),
        ladder_ok=Verdict.YES,
    )
    observation = await FakeRewriteJudge(scripted).judge(request)
    derived = derive_result(request, observation)
    assert (derived.softer, derived.firmer, derived.ladder_ok) == (
        scripted.softer,
        scripted.firmer,
        scripted.ladder_ok,
    )
