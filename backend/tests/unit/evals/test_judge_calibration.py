"""Clean-judge calibration: accuracy and defect-recall math, the pass rule, the run loop, files."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest
from tests.fakes.judge import JUDGE_USAGE, FakeRewriteJudge, judge_result

from svoi_pravila.application.errors import (
    GenerationRefusedByProvider,
    GenerationUnavailable,
    InvalidGenerationOutput,
    InvalidOutputReason,
    UnavailableKind,
)
from svoi_pravila.application.ports.generation import TokenUsage
from svoi_pravila.application.ports.rewrite_judge import JudgeObservation, Verdict
from svoi_pravila.benchmarks.runner import SpendTracker
from svoi_pravila.evals import judge_cli
from svoi_pravila.evals.judge_calibration import (
    RUBRIC_VERSION,
    ItemOutcome,
    SlotOutcome,
    calibrate,
    calibration_path,
    calibration_payload,
    clean_stats,
    disagreements,
    judge_is_calibrated,
    render_calibration_report,
    report_name,
    run_calibration,
    slot_request,
    write_calibration,
)
from svoi_pravila.evals.judge_cases import (
    SOURCE_EVAL_OUTPUT,
    SOURCE_SYNTHETIC,
    ExpectedClean,
    JudgeCase,
    load_judge_cases,
)

_USAGE = TokenUsage(input=3, output=4)
_DATA = Path(__file__).resolve().parents[3] / "evals" / "data" / "judge_calibration_v1.jsonl"
_DEFECTIVE = 20
_GOOD = 20


def _source(index: int) -> str:
    return SOURCE_EVAL_OUTPUT if index % 2 else SOURCE_SYNTHETIC


def _defective_case(case_id: str, source: str = SOURCE_SYNTHETIC) -> JudgeCase:
    """``softer`` is rude, ``firmer`` is clean."""
    return JudgeCase(case_id, source, "мягче", "твёрже", ExpectedClean(Verdict.NO, Verdict.YES))


def _good_case(case_id: str, source: str = SOURCE_SYNTHETIC) -> JudgeCase:
    """Both variants are clean."""
    return JudgeCase(case_id, source, "с", "ф", ExpectedClean(Verdict.YES, Verdict.YES))


def _item(case: JudgeCase, softer: Verdict, firmer: Verdict) -> ItemOutcome:
    return ItemOutcome(case, SlotOutcome(judge_result(softer)), SlotOutcome(judge_result(firmer)))


def _perfect(case: JudgeCase) -> ItemOutcome:
    return _item(case, case.expected.softer, case.expected.firmer)


def _missed(case: JudgeCase) -> ItemOutcome:
    """The rude ``softer`` is called clean."""
    return _item(case, Verdict.YES, case.expected.firmer)


def _outcomes(*, missed: int = 0, false_alarms: int = 0) -> list[ItemOutcome]:
    """Twenty defective and twenty good items judged perfectly, except the given errors.

    Items alternate between ``synthetic`` and ``eval_output``; errors are made on the first
    items of each block, so ``missed`` defects are missed and ``false_alarms`` good ``firmer``
    values are called rude.
    """
    outcomes: list[ItemOutcome] = []
    for index in range(_DEFECTIVE):
        case = _defective_case(f"c{index}", _source(index))
        outcomes.append(_missed(case) if index < missed else _perfect(case))
    for index in range(_GOOD):
        case = _good_case(f"g{index}", _source(index))
        outcomes.append(
            _item(case, Verdict.YES, Verdict.NO) if index < false_alarms else _perfect(case)
        )
    return outcomes


def _unavailable(kind: UnavailableKind) -> GenerationUnavailable:
    return GenerationUnavailable(
        kind, usage=_USAGE, attempts=1, model="m", prompt_version=RUBRIC_VERSION
    )


@pytest.mark.unit
def test_a_perfect_judge_passes() -> None:
    calibration = calibrate("m", _outcomes())
    assert calibration.passed
    assert calibration.rubric == RUBRIC_VERSION == "judge_clean@v2"
    assert calibration.items == _DEFECTIVE + _GOOD
    assert calibration.failed_calls == 0
    assert calibration.overall.accuracy == 1.0
    assert calibration.overall.defect_recall == 1.0


@pytest.mark.unit
def test_labels_count_two_values_per_item_and_defects_are_the_no_labels() -> None:
    stats = calibrate("m", _outcomes()).overall
    assert stats.labels == 2 * (_DEFECTIVE + _GOOD)
    assert stats.defects == _DEFECTIVE


@pytest.mark.unit
def test_recall_needs_ninety_five_percent() -> None:
    one = calibrate("m", _outcomes(missed=1))
    assert one.overall.defect_recall == 0.95
    assert one.passed
    two = calibrate("m", _outcomes(missed=2))
    assert two.overall.defect_recall == 0.9
    assert not two.passed


@pytest.mark.unit
def test_overall_accuracy_needs_ninety_percent() -> None:
    # 4 false alarms of 80 values: accuracy 0.95; 8 false alarms: 0.90 holds; 9: 0.8875 fails.
    assert calibrate("m", _outcomes(false_alarms=8)).overall.accuracy == 0.9
    assert calibrate("m", _outcomes(false_alarms=8)).passed
    nine = calibrate("m", _outcomes(false_alarms=9))
    assert nine.overall.defect_recall == 1.0
    assert nine.overall.accuracy == 0.8875
    assert not nine.passed


@pytest.mark.unit
def test_a_set_without_defects_cannot_pass() -> None:
    outcomes = [_perfect(_good_case(f"g{index}", _source(index))) for index in range(4)]
    calibration = calibrate("m", outcomes)
    assert calibration.overall.defect_recall is None
    assert not calibration.passed


@pytest.mark.unit
def test_calls_without_verdicts_reproduce_nothing_and_finds_no_defect() -> None:
    case = _defective_case("c")
    nothing = ItemOutcome(case, SlotOutcome(None), SlotOutcome(None))
    calibration = calibrate("m", [nothing])
    assert calibration.failed_calls == 2
    assert calibration.overall.accuracy == 0.0
    assert calibration.overall.defect_recall == 0.0
    assert len(disagreements([nothing])) == 2


@pytest.mark.unit
def test_disagreements_list_only_the_wrong_values() -> None:
    wrong = disagreements(_outcomes(missed=2))
    assert [(item.case.id, item.slot) for item in wrong] == [("c0", "softer"), ("c1", "softer")]
    assert wrong[0].expected is Verdict.NO
    assert wrong[0].got is Verdict.YES


@pytest.mark.unit
async def test_run_calibration_judges_each_variant_alone_and_counts_spend() -> None:
    cases = [_defective_case(f"c{index}") for index in range(3)]
    judge = FakeRewriteJudge()
    spend = SpendTracker()
    run = await run_calibration(judge, cases, deadline_seconds=5.0, max_tokens=10_000, spend=spend)
    assert [outcome.case.id for outcome in run.outcomes] == ["c0", "c1", "c2"]
    assert run.stop_reason is None
    assert run.spent_billable == 6 * JUDGE_USAGE.billable
    assert [request.text for request in judge.calls] == ["мягче", "твёрже"] * 3
    assert {request.deadline_seconds for request in judge.calls} == {5.0}


@pytest.mark.unit
async def test_run_calibration_derives_the_verdicts_and_the_report_shows_the_evidence() -> None:
    case = _defective_case("c0")
    clean = judge_result()
    observed = JudgeObservation(rude=("твёрже", "нет такого"), meta=clean.meta)
    judge = FakeRewriteJudge(clean, observed)
    run = await run_calibration(
        judge, [case], deadline_seconds=5.0, max_tokens=10_000, spend=SpendTracker()
    )
    outcome = run.outcomes[0]
    assert outcome.firmer.observation is observed
    assert outcome.softer.result is not None
    assert outcome.softer.result.clean is Verdict.YES
    assert outcome.firmer.result is not None
    assert outcome.firmer.result.clean is Verdict.NO
    report = render_calibration_report(
        calibrate("model-x", run.outcomes), run.outcomes, spent_billable=1
    )
    assert "| c0 | firmer | yes | no | твёрже; ?нет такого | твёрже |" in report
    assert "| c0 | softer | no | yes | - | мягче |" in report
    assert "- ungrounded quotes: 1 (softer 0, firmer 1)" in report
    payload = calibration_payload(calibrate("model-x", run.outcomes))
    assert payload["ungrounded"] == {"softer": 0, "firmer": 1, "total": 1}


@pytest.mark.unit
def test_a_slot_request_carries_the_text_of_one_variant_only() -> None:
    case = _defective_case("c0")
    assert slot_request(case, "softer", 5.0).text == "мягче"
    assert slot_request(case, "firmer", 5.0).text == "твёрже"
    assert slot_request(case, "firmer", 5.0).deadline_seconds == 5.0


@pytest.mark.unit
async def test_run_calibration_stops_before_an_item_that_could_cross_the_budget() -> None:
    cases = [_defective_case(f"c{index}") for index in range(3)]
    judge = FakeRewriteJudge()
    # An item needs 2 x 1_000 worst case; the first item spends 22.
    run = await run_calibration(
        judge, cases, deadline_seconds=5.0, max_tokens=2_021, spend=SpendTracker()
    )
    assert len(run.outcomes) == 1
    assert run.stop_reason == "token_budget"
    assert len(judge.calls) == 2


@pytest.mark.unit
async def test_failures_are_calls_without_verdicts_and_the_run_goes_on() -> None:
    cases = [_defective_case(f"c{index}") for index in range(3)]
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
    assert [(o.softer.result is None, o.firmer.result is None) for o in run.outcomes] == [
        (True, True),
        (True, False),
        (False, False),
    ]
    assert run.stop_reason is None
    assert run.spent_billable == 3 * _USAGE.billable + 3 * JUDGE_USAGE.billable
    assert calibrate("m", run.outcomes).failed_calls == 3


@pytest.mark.unit
@pytest.mark.parametrize("kind", [UnavailableKind.RATE_LIMITED, UnavailableKind.AUTH])
async def test_rate_limit_and_auth_stop_the_run_without_retry(kind: UnavailableKind) -> None:
    cases = [_defective_case(f"c{index}") for index in range(3)]
    judge = FakeRewriteJudge(judge_result(), _unavailable(kind))
    run = await run_calibration(
        judge, cases, deadline_seconds=5.0, max_tokens=10_000, spend=SpendTracker()
    )
    assert run.stop_reason == kind.value
    assert len(run.outcomes) == 1
    assert run.outcomes[0].softer.result is not None
    assert run.outcomes[0].firmer.result is None
    assert len(judge.calls) == 2


@pytest.mark.unit
async def test_a_stop_on_the_first_variant_skips_the_second_call() -> None:
    judge = FakeRewriteJudge(_unavailable(UnavailableKind.RATE_LIMITED))
    run = await run_calibration(
        judge,
        [_defective_case("c0"), _defective_case("c1")],
        deadline_seconds=5.0,
        max_tokens=10_000,
        spend=SpendTracker(),
    )
    assert run.stop_reason == UnavailableKind.RATE_LIMITED.value
    assert len(judge.calls) == 1
    assert len(run.outcomes) == 1
    assert run.outcomes[0].softer.result is None
    assert run.outcomes[0].firmer.result is None


@pytest.mark.unit
def test_the_numbers_are_given_per_source() -> None:
    calibration = calibrate("m", _outcomes())
    by_source = dict(calibration.by_source)
    assert set(by_source) == {SOURCE_SYNTHETIC, SOURCE_EVAL_OUTPUT}
    for stats in by_source.values():
        assert stats.labels == _DEFECTIVE + _GOOD
        assert stats.defects == _DEFECTIVE // 2


@pytest.mark.unit
def test_the_numbers_are_given_per_variant() -> None:
    calibration = calibrate("m", _outcomes(missed=2, false_alarms=3))
    by_slot = dict(calibration.by_slot)
    assert list(by_slot) == ["softer", "firmer"]
    assert by_slot["softer"].labels == by_slot["firmer"].labels == _DEFECTIVE + _GOOD
    assert (by_slot["softer"].defects, by_slot["softer"].defect_recall) == (_DEFECTIVE, 0.9)
    assert by_slot["softer"].accuracy == 0.95
    assert (by_slot["firmer"].defects, by_slot["firmer"].defect_recall) == (0, None)
    assert by_slot["firmer"].accuracy == 0.925


@pytest.mark.unit
def test_a_defect_missed_on_eval_output_fails_although_overall_passes() -> None:
    outcomes = _outcomes()
    case = outcomes[1].case
    assert case.source == SOURCE_EVAL_OUTPUT
    outcomes[1] = _missed(case)
    calibration = calibrate("m", outcomes)
    real = dict(calibration.by_source)[SOURCE_EVAL_OUTPUT]
    assert calibration.overall.defect_recall == 0.95
    assert real.defect_recall == 0.9
    assert not calibration.passed


@pytest.mark.unit
def test_eval_output_accuracy_needs_eighty_five_percent() -> None:
    # Good ``firmer`` values of eval_output items called rude: 7 of 40 there -> 33/40 = 0.825.
    outcomes = _outcomes()
    for index in range(_DEFECTIVE, _DEFECTIVE + _GOOD):
        case = outcomes[index].case
        if case.source == SOURCE_EVAL_OUTPUT and index - _DEFECTIVE < 14:
            outcomes[index] = _item(case, Verdict.YES, Verdict.NO)
    calibration = calibrate("m", outcomes)
    real = dict(calibration.by_source)[SOURCE_EVAL_OUTPUT]
    assert calibration.overall.accuracy == 0.9125
    assert real.accuracy == 0.825
    assert real.defect_recall == 1.0
    assert not calibration.passed


@pytest.mark.unit
def test_eval_output_accuracy_exactly_at_the_floor_passes() -> None:
    # 6 of 40 eval_output values wrong -> 34/40 = 0.85; overall 74/80 = 0.925.
    outcomes = _outcomes()
    wrong = 0
    for index in range(_DEFECTIVE, _DEFECTIVE + _GOOD):
        case = outcomes[index].case
        if case.source == SOURCE_EVAL_OUTPUT and wrong < 6:
            outcomes[index] = _item(case, Verdict.YES, Verdict.NO)
            wrong += 1
    calibration = calibrate("m", outcomes)
    assert dict(calibration.by_source)[SOURCE_EVAL_OUTPUT].accuracy == 0.85
    assert calibration.passed


@pytest.mark.unit
def test_without_eval_output_items_the_calibration_cannot_pass() -> None:
    outcomes = [_perfect(_defective_case(f"c{index}")) for index in range(_DEFECTIVE)] + [
        _perfect(_good_case(f"g{index}")) for index in range(_GOOD)
    ]
    calibration = calibrate("m", outcomes)
    assert calibration.overall.passes(min_accuracy=0.9, min_recall=0.95)
    assert not calibration.passed


@pytest.mark.unit
def test_clean_stats_of_no_outcomes_has_no_denominators() -> None:
    stats = clean_stats([])
    assert (stats.labels, stats.accuracy, stats.defects, stats.defect_recall) == (0, None, 0, None)


@pytest.mark.unit
def test_calibration_path_names_the_model_and_rejects_unsafe_names(tmp_path: Path) -> None:
    assert calibration_path(tmp_path, "GigaChat-3-Pro").name == (
        "judge_calibration_GigaChat-3-Pro_v2.json"
    )
    for model in ("../x", "a b", "", "a/b"):
        with pytest.raises(ValueError, match="cannot be used"):
            calibration_path(tmp_path, model)


@pytest.mark.unit
def test_the_local_report_name_carries_the_model_and_the_rubric() -> None:
    assert report_name("GigaChat-3-Pro") == "judge_calibration_GigaChat-3-Pro_v2.md"
    assert report_name("GigaChat-2-Max") != report_name("GigaChat-3-Pro")
    with pytest.raises(ValueError, match="cannot be used"):
        report_name("../x")


@pytest.mark.unit
def test_the_cli_writes_the_report_to_a_path_with_the_model_unless_told_otherwise() -> None:
    default = judge_cli._report_path(argparse.Namespace(out=None, model="GigaChat-3-Pro"))
    assert default.name == "judge_calibration_GigaChat-3-Pro_v2.md"
    given = judge_cli._report_path(argparse.Namespace(out="reports/x.md", model="GigaChat-3-Pro"))
    assert given == Path("reports/x.md")


@pytest.mark.unit
def test_the_committed_payload_has_numbers_and_no_text(tmp_path: Path) -> None:
    calibration = calibrate("model-x", _outcomes())
    path = write_calibration(tmp_path, calibration)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload == calibration_payload(calibration)
    assert payload["rubric"] == RUBRIC_VERSION
    assert payload["model"] == "model-x"
    assert payload["passed"] is True
    assert payload["version"] == 5
    assert set(payload["clean"]) == {"labels", "accuracy", "defects", "defect_recall"}
    assert set(payload["sources"]) == {SOURCE_SYNTHETIC, SOURCE_EVAL_OUTPUT}
    assert set(payload["sources"][SOURCE_EVAL_OUTPUT]) == set(payload["clean"])
    assert set(payload["slots"]) == {"softer", "firmer"}
    assert set(payload["slots"]["softer"]) == set(payload["clean"])
    assert payload["failed_calls"] == 0
    assert payload["ungrounded"] == {"softer": 0, "firmer": 0, "total": 0}
    for text in ("мягче", "твёрже"):
        assert text not in path.read_text(encoding="utf-8")


@pytest.mark.unit
def test_judge_is_calibrated_only_with_a_passed_file_of_the_current_rubric(tmp_path: Path) -> None:
    assert not judge_is_calibrated(tmp_path, "model-x")
    write_calibration(tmp_path, calibrate("model-x", _outcomes(missed=4)))
    assert not judge_is_calibrated(tmp_path, "model-x")
    write_calibration(tmp_path, calibrate("model-x", _outcomes()))
    assert judge_is_calibrated(tmp_path, "model-x")
    assert not judge_is_calibrated(tmp_path, "other-model")

    path = calibration_path(tmp_path, "model-x")
    payload = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps(payload | {"rubric": "judge_clean@v0"}), encoding="utf-8")
    assert not judge_is_calibrated(tmp_path, "model-x")
    path.write_text(json.dumps(payload | {"model": "other-model"}), encoding="utf-8")
    assert not judge_is_calibrated(tmp_path, "model-x")


@pytest.mark.unit
@pytest.mark.parametrize("content", ["[]", '{"version": 4, "passed": true}', '{"version": 5}'])
def test_a_calibration_file_of_another_shape_is_an_error(tmp_path: Path, content: str) -> None:
    calibration_path(tmp_path, "model-x").write_text(content, encoding="utf-8")
    with pytest.raises(ValueError, match="not a version 5"):
        judge_is_calibrated(tmp_path, "model-x")


@pytest.mark.unit
def test_the_local_report_lists_disagreements_with_their_texts() -> None:
    outcomes = _outcomes(missed=1)
    calibration = calibrate("model-x", outcomes)
    report = render_calibration_report(calibration, outcomes, spent_billable=220)
    assert "- model: model-x" in report
    assert f"- rubric: {RUBRIC_VERSION}" in report
    assert "- billable tokens: 220" in report
    assert "| overall | 80 | 98.8 % | 20 | 95.0 % | PASS |" in report
    assert "| softer | 40 |" in report
    assert "| firmer | 40 |" in report
    assert "| c0 | softer | no | yes | - | мягче |" in report


@pytest.mark.unit
def test_the_report_marks_a_set_without_data() -> None:
    outcomes = [_perfect(_good_case("g"))]
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
        "limit": None,
    }
    return argparse.Namespace(**(values | overrides))


@pytest.mark.unit
async def test_dry_run_prints_the_plan_without_network(capsys: pytest.CaptureFixture[str]) -> None:
    assert await judge_cli.async_main(_args(dry_run=True)) == 0
    out = capsys.readouterr().out
    count = len(load_judge_cases(_DATA))
    assert f"Items: {count}" in out
    assert f"Total planned calls: {2 * count}" in out
    assert "Total worst-case billable tokens:" in out


@pytest.mark.unit
async def test_a_limit_plans_only_the_first_items(capsys: pytest.CaptureFixture[str]) -> None:
    assert await judge_cli.async_main(_args(dry_run=True, limit=10)) == 0
    out = capsys.readouterr().out
    assert "Items: 10" in out
    assert "Total planned calls: 20" in out
    assert [case.id for case in judge_cli._cases(_args(limit=3))] == [
        case.id for case in load_judge_cases(_DATA)[:3]
    ]


@pytest.mark.unit
async def test_a_live_run_needs_a_token_budget() -> None:
    with pytest.raises(SystemExit, match="--max-tokens is required"):
        await judge_cli.async_main(_args())
