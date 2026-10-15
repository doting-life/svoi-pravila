"""Judge calibration: accuracy and defect-recall math, the pass rule, the run loop, the files."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from tests.fakes.judge import JUDGE_USAGE, FakeRewriteJudge, judge_result, slot

from svoi_pravila.application.errors import (
    GenerationRefusedByProvider,
    GenerationUnavailable,
    InvalidGenerationOutput,
    InvalidOutputReason,
    UnavailableKind,
)
from svoi_pravila.application.ports.generation import RuleContext, TokenUsage
from svoi_pravila.application.ports.rewrite_judge import JudgeResult, Verdict
from svoi_pravila.benchmarks.runner import SpendTracker
from svoi_pravila.domain.enums import RewriteGoal, RuleCategory
from svoi_pravila.evals import judge_cli
from svoi_pravila.evals.judge_calibration import (
    CRITERIA,
    RUBRIC_VERSION,
    ItemOutcome,
    calibrate,
    calibration_path,
    calibration_payload,
    criterion_stats,
    defect_value,
    disagreements,
    judge_is_calibrated,
    render_calibration_report,
    run_calibration,
    write_calibration,
)
from svoi_pravila.evals.judge_cases import (
    SOURCE_EVAL_OUTPUT,
    SOURCE_SYNTHETIC,
    ExpectedVerdicts,
    JudgeCase,
    load_judge_cases,
)

_USAGE = TokenUsage(input=3, output=4)
_DATA = Path(__file__).resolve().parents[3] / "evals" / "data" / "judge_calibration_v1.jsonl"
_RULE = RuleContext(RuleCategory.HOW_TO_ASK, "правило", datetime(2026, 1, 1, tzinfo=UTC))
_ITEMS = 20
_PAIRS = 20
_SLOT_CRITERIA = ("clean", "meaning_kept", "invented", "goal_done", "rule_done")

_ALL_BAD = slot(
    clean=Verdict.NO,
    meaning_kept=Verdict.NO,
    invented=Verdict.YES,
    goal_done=Verdict.NO,
    rule_done=Verdict.NO,
)
_ALL_GOOD = slot(goal_done=Verdict.YES, rule_done=Verdict.YES)


def _source(index: int) -> str:
    return SOURCE_EVAL_OUTPUT if index % 2 else SOURCE_SYNTHETIC


def _full_case(case_id: str, source: str = SOURCE_SYNTHETIC) -> JudgeCase:
    """``softer`` is defective in every criterion, ``firmer`` is good in every criterion.

    The pair is not eligible, so its ``ladder_ok`` label is not measured.
    """
    return JudgeCase(
        id=case_id,
        source=source,
        draft="черновик",
        goal=RewriteGoal.DECLINE,
        rules=(_RULE,),
        softer="мягче",
        firmer="твёрже",
        expected=ExpectedVerdicts(_ALL_BAD, _ALL_GOOD, Verdict.NO),
    )


def _pair_case(case_id: str, source: str) -> JudgeCase:
    """An eligible pair: every slot label is good and the ladder is broken."""
    return JudgeCase(
        id=case_id,
        source=source,
        draft="черновик",
        goal=RewriteGoal.DECLINE,
        rules=(_RULE,),
        softer="мягче",
        firmer="твёрже",
        expected=ExpectedVerdicts(_ALL_GOOD, _ALL_GOOD, Verdict.NO),
    )


def _plain_case(case_id: str, source: str) -> JudgeCase:
    """No goal and no rules: an eligible pair whose ladder is fine."""
    return JudgeCase(
        case_id, source, "д", None, (), "с", "ф", ExpectedVerdicts(slot(), slot(), Verdict.YES)
    )


def _perfect(case: JudgeCase) -> JudgeResult:
    return judge_result(
        softer=case.expected.softer, firmer=case.expected.firmer, ladder_ok=case.expected.ladder_ok
    )


def _outcomes(*, flip: str | None = None, flips: int = 0) -> list[ItemOutcome]:
    """Twenty defective and twenty eligible items judged perfectly, except ``flips`` answers.

    Items alternate between ``synthetic`` and ``eval_output``. A defect criterion flips on
    ``softer`` of the defective items (the defect is missed); ``ladder_ok`` flips on the eligible
    items.
    """
    outcomes: list[ItemOutcome] = []
    for index in range(_ITEMS):
        case = _full_case(f"c{index}", _source(index))
        result = _perfect(case)
        if flip is not None and flip != "ladder_ok" and index < flips:
            result = _flipped(case, flip)
        outcomes.append(ItemOutcome(case, result))
    for index in range(_PAIRS):
        pair = _pair_case(f"p{index}", _source(index))
        result = _perfect(pair)
        if flip == "ladder_ok" and index < flips:
            result = judge_result(pair.expected.softer, pair.expected.firmer, Verdict.YES)
        outcomes.append(ItemOutcome(pair, result))
    return outcomes


def _flipped(case: JudgeCase, flip: str) -> JudgeResult:
    """The perfect answer with the softer value of ``flip`` turned to its non-defect."""
    expected = case.expected
    good = Verdict.NO if flip == "invented" else Verdict.YES
    values = {name: getattr(expected.softer, name) for name in _SLOT_CRITERIA} | {flip: good}
    return judge_result(slot(**values), expected.firmer, expected.ladder_ok)


def _unavailable(kind: UnavailableKind) -> GenerationUnavailable:
    return GenerationUnavailable(
        kind, usage=_USAGE, attempts=1, model="m", prompt_version=RUBRIC_VERSION
    )


@pytest.mark.unit
def test_defect_value_is_yes_for_invented_and_no_otherwise() -> None:
    assert defect_value("invented") is Verdict.YES
    for criterion in ("clean", "meaning_kept", "goal_done", "rule_done", "ladder_ok"):
        assert defect_value(criterion) is Verdict.NO


@pytest.mark.unit
def test_a_perfect_judge_passes_every_criterion() -> None:
    calibration = calibrate("m", _outcomes())
    assert calibration.passed
    assert calibration.items == _ITEMS + _PAIRS
    assert calibration.failed_items == 0
    for stats in calibration.criteria:
        assert stats.accuracy == 1.0
        assert stats.defect_recall == 1.0
    assert [stats.criterion for stats in calibration.criteria] == list(CRITERIA)


@pytest.mark.unit
def test_labels_per_criterion_count_two_slots_and_one_pair() -> None:
    stats = {item.criterion: item for item in calibrate("m", _outcomes()).criteria}
    assert stats["clean"].labels == 2 * (_ITEMS + _PAIRS)
    assert stats["clean"].defects == _ITEMS
    assert stats["ladder_ok"].labels == _PAIRS
    assert stats["ladder_ok"].defects == _PAIRS


@pytest.mark.unit
@pytest.mark.parametrize("criterion", CRITERIA)
def test_recall_below_the_floor_fails_that_criterion(criterion: str) -> None:
    # 4 of 20 missed defects: recall 0.80 < 0.85.
    stats = {
        item.criterion: item for item in calibrate("m", _outcomes(flip=criterion, flips=4)).criteria
    }
    assert stats[criterion].defect_recall == 0.8
    assert not stats[criterion].passes()
    assert not calibrate("m", _outcomes(flip=criterion, flips=4)).passed


@pytest.mark.unit
def test_recall_exactly_at_the_floor_passes_except_for_clean() -> None:
    # 3 of 20 missed: recall 0.85; accuracy of the slot criteria 77/80 stays above 0.90.
    for criterion in ("meaning_kept", "invented", "goal_done", "rule_done"):
        stats = {
            item.criterion: item
            for item in calibrate("m", _outcomes(flip=criterion, flips=3)).criteria
        }
        assert stats[criterion].defect_recall == 0.85
        assert stats[criterion].passes()
    clean = {
        item.criterion: item for item in calibrate("m", _outcomes(flip="clean", flips=3)).criteria
    }["clean"]
    assert clean.defect_recall == 0.85
    assert not clean.passes()


@pytest.mark.unit
def test_clean_needs_ninety_five_percent_recall() -> None:
    one_missed = {
        item.criterion: item for item in calibrate("m", _outcomes(flip="clean", flips=1)).criteria
    }["clean"]
    assert one_missed.defect_recall == 0.95
    assert one_missed.passes()


@pytest.mark.unit
def test_accuracy_below_ninety_percent_fails_even_with_full_recall() -> None:
    outcomes: list[ItemOutcome] = []
    for index in range(_ITEMS):
        case = _full_case(f"c{index}")
        # the good ``firmer`` is called defective on six items: accuracy 34/40 = 0.85
        firmer = _ALL_BAD if index < 6 else case.expected.firmer
        outcomes.append(
            ItemOutcome(case, judge_result(case.expected.softer, firmer, case.expected.ladder_ok))
        )
    clean = {item.criterion: item for item in calibrate("m", outcomes).criteria}["clean"]
    assert clean.defect_recall == 1.0
    assert clean.accuracy == 0.85
    assert not clean.passes()


@pytest.mark.unit
def test_a_criterion_without_defects_cannot_pass() -> None:
    case = _plain_case("c", SOURCE_SYNTHETIC)
    calibration = calibrate("m", [ItemOutcome(case, judge_result())])
    stats = {item.criterion: item for item in calibration.criteria}
    assert stats["clean"].defect_recall is None
    assert stats["goal_done"].labels == 2
    assert stats["goal_done"].accuracy == 1.0
    assert not calibration.passed


@pytest.mark.unit
def test_accuracy_counts_na_as_a_label() -> None:
    case = _plain_case("c", SOURCE_SYNTHETIC)
    result = judge_result(softer=slot(goal_done=Verdict.YES))
    stats = criterion_stats([ItemOutcome(case, result)], "goal_done")
    assert stats.accuracy == 0.5


@pytest.mark.unit
def test_an_item_without_verdicts_reproduces_nothing_and_finds_no_defect() -> None:
    case = _full_case("c")
    calibration = calibrate("m", [ItemOutcome(case, None)])
    assert calibration.failed_items == 1
    stats = {item.criterion: item for item in calibration.criteria}
    assert stats["clean"].accuracy == 0.0
    assert stats["clean"].defect_recall == 0.0
    assert len(disagreements([ItemOutcome(case, None)])) == 2 * 5


@pytest.mark.unit
def test_disagreements_list_only_the_wrong_values() -> None:
    outcomes = _outcomes(flip="clean", flips=2)
    wrong = disagreements(outcomes)
    assert [(item.case.id, item.slot, item.criterion) for item in wrong] == [
        ("c0", "softer", "clean"),
        ("c1", "softer", "clean"),
    ]
    assert wrong[0].expected is Verdict.NO
    assert wrong[0].got is Verdict.YES


@pytest.mark.unit
async def test_run_calibration_judges_every_item_once_and_counts_spend() -> None:
    cases = [_full_case(f"c{index}") for index in range(3)]
    judge = FakeRewriteJudge()
    spend = SpendTracker()
    run = await run_calibration(judge, cases, deadline_seconds=5.0, max_tokens=10_000, spend=spend)
    assert [outcome.case.id for outcome in run.outcomes] == ["c0", "c1", "c2"]
    assert run.stop_reason is None
    assert run.spent_billable == 3 * JUDGE_USAGE.billable
    assert [request.draft for request in judge.calls] == ["черновик"] * 3
    assert judge.calls[0].deadline_seconds == 5.0
    assert judge.calls[0].rules == (_RULE,)


@pytest.mark.unit
async def test_run_calibration_stops_before_a_call_that_could_cross_the_budget() -> None:
    cases = [_full_case(f"c{index}") for index in range(3)]
    judge = FakeRewriteJudge()
    run = await run_calibration(
        judge, cases, deadline_seconds=5.0, max_tokens=1_005, spend=SpendTracker()
    )
    assert len(run.outcomes) == 1
    assert run.stop_reason == "token_budget"
    assert len(judge.calls) == 1


@pytest.mark.unit
async def test_failures_are_items_without_verdicts_and_the_run_goes_on() -> None:
    cases = [_full_case(f"c{index}") for index in range(4)]
    judge = FakeRewriteJudge(
        InvalidGenerationOutput(
            (InvalidOutputReason.EMPTY_MESSAGE,),
            usage=_USAGE,
            attempts=2,
            model="m",
            prompt_version=RUBRIC_VERSION,
        ),
        GenerationRefusedByProvider(
            usage=_USAGE, attempts=1, model="m", prompt_version=RUBRIC_VERSION
        ),
        _unavailable(UnavailableKind.TIMEOUT),
        judge_result(),
    )
    run = await run_calibration(
        judge, cases, deadline_seconds=5.0, max_tokens=10_000, spend=SpendTracker()
    )
    assert [outcome.result is None for outcome in run.outcomes] == [True, True, True, False]
    assert run.stop_reason is None
    assert run.spent_billable == 3 * _USAGE.billable + JUDGE_USAGE.billable


@pytest.mark.unit
@pytest.mark.parametrize("kind", [UnavailableKind.RATE_LIMITED, UnavailableKind.AUTH])
async def test_rate_limit_and_auth_stop_the_run_without_retry(kind: UnavailableKind) -> None:
    cases = [_full_case(f"c{index}") for index in range(3)]
    judge = FakeRewriteJudge(judge_result(), _unavailable(kind))
    run = await run_calibration(
        judge, cases, deadline_seconds=5.0, max_tokens=10_000, spend=SpendTracker()
    )
    assert run.stop_reason == kind.value
    assert len(run.outcomes) == 2
    assert len(judge.calls) == 2


@pytest.mark.unit
def test_ladder_ok_is_measured_only_on_eligible_pairs() -> None:
    outcomes = _outcomes()
    wrong = [
        ItemOutcome(
            outcome.case,
            judge_result(outcome.case.expected.softer, outcome.case.expected.firmer, Verdict.YES),
        )
        for outcome in outcomes[:_ITEMS]
    ]
    stats = criterion_stats(wrong + outcomes[_ITEMS:], "ladder_ok")
    assert stats.labels == _PAIRS
    assert stats.accuracy == 1.0
    assert not [item for item in disagreements(wrong) if item.criterion == "ladder_ok"]


@pytest.mark.unit
def test_an_eligible_pair_with_a_missed_ladder_defect_is_counted() -> None:
    calibration = calibrate("m", _outcomes(flip="ladder_ok", flips=4))
    stats = {item.criterion: item for item in calibration.criteria}["ladder_ok"]
    assert stats.labels == _PAIRS
    assert stats.defect_recall == 0.8
    assert not calibration.passed


@pytest.mark.unit
def test_the_numbers_are_given_per_source() -> None:
    calibration = calibrate("m", _outcomes())
    by_source = dict(calibration.by_source)
    assert set(by_source) == {SOURCE_SYNTHETIC, SOURCE_EVAL_OUTPUT}
    for criteria in by_source.values():
        stats = {item.criterion: item for item in criteria}
        assert stats["clean"].labels == _ITEMS + _PAIRS
        assert stats["ladder_ok"].labels == _PAIRS // 2


@pytest.mark.unit
def test_a_clean_defect_missed_on_eval_output_fails_although_overall_passes() -> None:
    outcomes = _outcomes()
    case = outcomes[1].case
    assert case.source == SOURCE_EVAL_OUTPUT
    outcomes[1] = ItemOutcome(case, _flipped(case, "clean"))
    calibration = calibrate("m", outcomes)
    overall = {item.criterion: item for item in calibration.criteria}["clean"]
    real = {item.criterion: item for item in dict(calibration.by_source)[SOURCE_EVAL_OUTPUT]}[
        "clean"
    ]
    assert overall.passes()
    assert real.defect_recall == 0.9
    assert not calibration.passed


@pytest.mark.unit
def test_accuracy_below_eighty_five_percent_on_eval_output_fails_although_overall_passes() -> None:
    outcomes = _outcomes()
    # seven good ``firmer`` slots of eval_output items called defective: 33/40 = 0.825 there,
    # 73/80 = 0.9125 overall, and no defect is missed.
    for index in range(1, 2 * 7, 2):
        case = outcomes[index].case
        assert case.source == SOURCE_EVAL_OUTPUT
        wrong = judge_result(case.expected.softer, _ALL_BAD, case.expected.ladder_ok)
        outcomes[index] = ItemOutcome(case, wrong)
    calibration = calibrate("m", outcomes)
    stats = {item.criterion: item for item in calibration.criteria}
    real = {item.criterion: item for item in dict(calibration.by_source)[SOURCE_EVAL_OUTPUT]}
    assert stats["meaning_kept"].passes()
    assert real["meaning_kept"].accuracy == 0.825
    assert not real["meaning_kept"].passes_real()
    assert not calibration.passed


@pytest.mark.unit
def test_without_eval_output_items_the_calibration_cannot_pass() -> None:
    outcomes = [
        ItemOutcome(_full_case(f"c{index}"), _perfect(_full_case(f"c{index}")))
        for index in range(_ITEMS)
    ] + [
        ItemOutcome(
            _pair_case(f"p{index}", SOURCE_SYNTHETIC),
            _perfect(_pair_case(f"p{index}", SOURCE_SYNTHETIC)),
        )
        for index in range(_PAIRS)
    ]
    calibration = calibrate("m", outcomes)
    assert all(stats.passes() for stats in calibration.criteria[:-1])
    assert not calibration.passed


@pytest.mark.unit
def test_calibration_path_names_the_model_and_rejects_unsafe_names(tmp_path: Path) -> None:
    assert calibration_path(tmp_path, "GigaChat-3-Lightning").name == (
        "judge_calibration_GigaChat-3-Lightning_v1.json"
    )
    for model in ("../x", "a b", "", "a/b"):
        with pytest.raises(ValueError, match="cannot be used"):
            calibration_path(tmp_path, model)


@pytest.mark.unit
def test_the_committed_payload_has_numbers_and_no_text(tmp_path: Path) -> None:
    calibration = calibrate("model-x", _outcomes())
    path = write_calibration(tmp_path, calibration)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload == calibration_payload(calibration)
    assert payload["rubric"] == RUBRIC_VERSION
    assert payload["model"] == "model-x"
    assert payload["passed"] is True
    assert payload["version"] == 2
    assert set(payload["criteria"]) == set(CRITERIA)
    assert set(payload["sources"]) == {SOURCE_SYNTHETIC, SOURCE_EVAL_OUTPUT}
    assert set(payload["sources"][SOURCE_EVAL_OUTPUT]) == set(CRITERIA)
    assert set(payload["criteria"]["clean"]) == {"labels", "accuracy", "defects", "defect_recall"}
    for text in ("черновик", "мягче", "твёрже"):
        assert text not in path.read_text(encoding="utf-8")


@pytest.mark.unit
def test_judge_is_calibrated_only_with_a_passed_file_of_the_current_rubric(tmp_path: Path) -> None:
    assert not judge_is_calibrated(tmp_path, "model-x")
    write_calibration(tmp_path, calibrate("model-x", _outcomes(flip="clean", flips=4)))
    assert not judge_is_calibrated(tmp_path, "model-x")
    write_calibration(tmp_path, calibrate("model-x", _outcomes()))
    assert judge_is_calibrated(tmp_path, "model-x")
    assert not judge_is_calibrated(tmp_path, "other-model")

    path = calibration_path(tmp_path, "model-x")
    payload = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps(payload | {"rubric": "judge_rewrite@v0"}), encoding="utf-8")
    assert not judge_is_calibrated(tmp_path, "model-x")
    path.write_text(json.dumps(payload | {"model": "other-model"}), encoding="utf-8")
    assert not judge_is_calibrated(tmp_path, "model-x")


@pytest.mark.unit
@pytest.mark.parametrize("content", ["[]", '{"version": 1, "passed": true}', '{"version": 2}'])
def test_a_calibration_file_of_another_shape_is_an_error(tmp_path: Path, content: str) -> None:
    calibration_path(tmp_path, "model-x").write_text(content, encoding="utf-8")
    with pytest.raises(ValueError, match="not a version 2"):
        judge_is_calibrated(tmp_path, "model-x")


@pytest.mark.unit
def test_the_local_report_lists_disagreements_with_their_texts() -> None:
    outcomes = _outcomes(flip="meaning_kept", flips=1)
    calibration = calibrate("model-x", outcomes)
    report = render_calibration_report(calibration, outcomes, spent_billable=220)
    assert "- model: model-x" in report
    assert f"- rubric: {RUBRIC_VERSION}" in report
    assert "- billable tokens: 220" in report
    assert "| meaning_kept | 80 | 98.8 % | 20 | 95.0 % | PASS |" in report
    assert "| c0 | softer | meaning_kept | no | yes | черновик | мягче | твёрже |" in report


@pytest.mark.unit
def test_the_report_marks_criteria_without_data() -> None:
    case = _plain_case("c", SOURCE_SYNTHETIC)
    outcomes = [ItemOutcome(case, judge_result())]
    report = render_calibration_report(calibrate("m", outcomes), outcomes, spent_billable=0)
    assert "no data" in report
    assert "- passed: no" in report


def _args(**overrides: object) -> argparse.Namespace:
    values: dict[str, object] = {
        "model": "model-x",
        "data": str(_DATA),
        "baselines_dir": "unused",
        "deadline": 30.0,
        "out": "unused",
        "dry_run": False,
        "list_models": False,
        "max_tokens": None,
    }
    return argparse.Namespace(**(values | overrides))


@pytest.mark.unit
async def test_dry_run_prints_the_plan_without_network(capsys: pytest.CaptureFixture[str]) -> None:
    assert await judge_cli.async_main(_args(dry_run=True)) == 0
    out = capsys.readouterr().out
    count = len(load_judge_cases(_DATA))
    assert f"Items: {count}" in out
    assert f"Total planned calls: {count}" in out
    assert "Total worst-case billable tokens:" in out


@pytest.mark.unit
async def test_a_live_run_needs_a_token_budget() -> None:
    with pytest.raises(SystemExit, match="--max-tokens is required"):
        await judge_cli.async_main(_args())
