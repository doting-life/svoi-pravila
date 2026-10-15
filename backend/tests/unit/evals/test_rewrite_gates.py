"""Offline proof of gates G3-G9 and J1-J6: recorded runs make each gate PASS, FAIL or n/a."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from svoi_pravila.application.ports.rewrite_judge import SlotVerdicts, Verdict
from svoi_pravila.domain.enums import RewriteGoal, VariantKind
from svoi_pravila.domain.variant_policy import PolicyRule
from svoi_pravila.evals.rewrite_cases import RewriteCategory
from svoi_pravila.evals.rewrite_gates import (
    Baseline,
    GateMetrics,
    GateResult,
    GateStatus,
    JudgeMetrics,
    baseline_payload,
    compute_metrics,
    evaluate_gates,
    gates_failed,
    read_baseline,
    write_baseline,
)
from svoi_pravila.evals.rewrite_report import render_report
from svoi_pravila.evals.rewrite_run import (
    ModelEval,
    RewriteEvalResult,
    RewriteRunRecord,
    RunJudgement,
    RunOutcome,
    RunVariant,
)


def _variant(
    kind: VariantKind,
    *rules: PolicyRule,
    check_ok: bool | None = None,
    stem_leak: bool | None = None,
    closing_ask_ok: bool | None = None,
    text: str = "text",
) -> RunVariant:
    return RunVariant(
        kind,
        text,
        rules,
        check_ok=check_ok,
        stem_leak=stem_leak,
        closing_ask_ok=closing_ask_ok,
    )


def _record(
    *,
    check: tuple[PolicyRule, ...] = (),
    softer: tuple[PolicyRule, ...] = (),
    firmer: tuple[PolicyRule, ...] = (),
    check_ok: bool | None = None,
    stem_leak: bool | None = None,
    had_rules: bool = False,
    outcome: RunOutcome = RunOutcome.OK,
    goal: RewriteGoal | None = None,
    closing_ask_ok: bool | None = None,
    judgement: RunJudgement | None = None,
    judge_failed: bool = False,
    case_id: str = "c",
    run_index: int = 1,
    texts: tuple[str, str] = ("text", "text"),
) -> RewriteRunRecord:
    variants = (
        ()
        if outcome is not RunOutcome.OK
        else (
            _variant(VariantKind.CHECK, *check, check_ok=check_ok, closing_ask_ok=closing_ask_ok),
            _variant(
                VariantKind.SOFTER,
                *softer,
                stem_leak=stem_leak,
                closing_ask_ok=closing_ask_ok,
                text=texts[0],
            ),
            _variant(
                VariantKind.FIRMER,
                *firmer,
                stem_leak=stem_leak,
                closing_ask_ok=closing_ask_ok,
                text=texts[1],
            ),
        )
    )
    return RewriteRunRecord(
        case_id=case_id,
        category=RewriteCategory.BASE,
        goal=goal,
        had_rules=had_rules,
        run_index=run_index,
        outcome=outcome,
        variants=variants,
        latency_ms=1,
        billable_tokens=1,
        judgement=judgement,
        judge_failed=judge_failed,
    )


def _clean(count: int) -> list[RewriteRunRecord]:
    return [_record(check_ok=True) for _ in range(count)]


def _by_gate(
    records: list[RewriteRunRecord],
    baseline: Baseline | None = None,
    *,
    judge_calibrated: bool = True,
) -> dict[str, GateResult]:
    results = evaluate_gates(compute_metrics(records), baseline, judge_calibrated=judge_calibrated)
    return {result.gate: result for result in results}


_BASELINE = Baseline(check_ok_rate=1.0)


@pytest.mark.unit
def test_clean_pass_makes_every_gate_pass_and_g8_needs_a_baseline() -> None:
    gates = _by_gate(_clean(20), judge_calibrated=False)
    assert list(gates) == ["G3", "G4", "G6", "G7", "G8", "G9", "J1", "J2", "J3", "J4", "J5", "J6"]
    assert [g.status for g in gates.values()] == [
        *[GateStatus.PASSED] * 4,
        GateStatus.NOT_APPLICABLE,
        GateStatus.PASSED,
        *[GateStatus.NOT_APPLICABLE] * 6,
    ]
    assert not gates_failed(tuple(gates.values()))
    with_baseline = _by_gate(_clean(20), _BASELINE)
    assert with_baseline["G8"].status is GateStatus.PASSED


@pytest.mark.unit
@pytest.mark.parametrize(
    ("gate", "rule", "kind"),
    [
        ("G4", PolicyRule.REGISTER, VariantKind.SOFTER),
        ("G4", PolicyRule.REGISTER, VariantKind.CHECK),
    ],
)
def test_zero_gates_fail_on_a_single_violating_call(
    gate: str, rule: PolicyRule, kind: VariantKind
) -> None:
    violating = _record(
        check=(rule,) if kind is VariantKind.CHECK else (),
        softer=(rule,) if kind is VariantKind.SOFTER else (),
        firmer=(rule,) if kind is VariantKind.FIRMER else (),
        check_ok=True,
    )
    gates = _by_gate([*_clean(19), violating])
    assert gates[gate].status is GateStatus.FAILED
    assert gates[gate].value == "1 of 20 calls"
    assert gates_failed(tuple(gates.values()))
    assert _by_gate(_clean(20))[gate].status is GateStatus.PASSED


@pytest.mark.unit
def test_g3_fails_on_a_forbidden_stem_in_softer_or_firmer_only() -> None:
    leaking = _record(stem_leak=True, check_ok=True)
    assert _by_gate([*_clean(19), leaking])["G3"].status is GateStatus.FAILED
    clean_stems = _record(stem_leak=False, check_ok=True)
    assert _by_gate([*_clean(19), clean_stems])["G3"].status is GateStatus.PASSED


@pytest.mark.unit
def test_g6_requires_ninety_percent_of_calls_without_a_longer_firmer() -> None:
    longer = _record(firmer=(PolicyRule.LADDER_LENGTH,), check_ok=True)
    assert _by_gate([*_clean(9), longer])["G6"].status is GateStatus.PASSED
    assert _by_gate([*_clean(8), longer, longer])["G6"].status is GateStatus.FAILED


@pytest.mark.unit
def test_g7_requires_ninety_five_percent_of_calls_with_distinct_variants() -> None:
    similar = _record(firmer=(PolicyRule.LADDER_SIMILAR,), check_ok=True)
    assert _by_gate([*_clean(19), similar])["G7"].status is GateStatus.PASSED
    assert _by_gate([*_clean(18), similar, similar])["G7"].status is GateStatus.FAILED


@pytest.mark.unit
def test_g8_compares_check_ok_with_the_baseline_and_limits_edit_ratio() -> None:
    worse = [_record(check_ok=False), *_clean(3)]
    assert _by_gate(worse, _BASELINE)["G8"].status is GateStatus.FAILED
    assert _by_gate(worse, Baseline(check_ok_rate=0.75))["G8"].status is GateStatus.PASSED
    edited = _record(check=(PolicyRule.EDIT_RATIO,), check_ok=True)
    assert _by_gate([*_clean(19), edited], _BASELINE)["G8"].status is GateStatus.PASSED
    assert _by_gate([*_clean(18), edited, edited], _BASELINE)["G8"].status is GateStatus.FAILED
    no_reference = [_record() for _ in range(5)]
    assert _by_gate(no_reference, _BASELINE)["G8"].status is GateStatus.FAILED
    assert _by_gate(no_reference)["G8"].status is GateStatus.NOT_APPLICABLE


@pytest.mark.unit
@pytest.mark.parametrize(
    "outcome", [RunOutcome.INVALID_OUTPUT, RunOutcome.NOT_OK_SAFETY, RunOutcome.REFUSED]
)
def test_g9_limits_unusable_answers_to_three_percent_of_calls(outcome: RunOutcome) -> None:
    unusable = _record(outcome=outcome)
    assert _by_gate([*_clean(99), unusable])["G9"].status is GateStatus.PASSED
    assert (
        _by_gate([*_clean(96), unusable, unusable, unusable, unusable])["G9"].status
        is GateStatus.FAILED
    )


@pytest.mark.unit
def test_g9_leaves_unavailable_calls_out_of_the_denominator_and_counts_them() -> None:
    unusable = _record(outcome=RunOutcome.REFUSED)
    unavailable = _record(outcome=RunOutcome.UNAVAILABLE)
    records = [*_clean(99), unusable, *([unavailable] * 50)]
    metrics = compute_metrics(records)
    assert metrics.unavailable_runs == 50
    assert metrics.runs == 150
    assert metrics.unusable_share == 0.01
    assert _by_gate(records)["G9"].status is GateStatus.PASSED
    only_unavailable = _by_gate([unavailable, unavailable])["G9"]
    assert only_unavailable.status is GateStatus.FAILED
    assert only_unavailable.value == "no data"


@pytest.mark.unit
def test_a_pass_without_ok_calls_fails_every_gate_that_needs_data() -> None:
    gates = _by_gate([_record(outcome=RunOutcome.REFUSED)])
    statuses = {gate: result.status for gate, result in gates.items()}
    assert statuses["G3"] is statuses["G6"] is statuses["G7"] is GateStatus.FAILED
    assert gates["G3"].value == "no data"
    assert statuses["J1"] is statuses["J5"] is statuses["J6"] is GateStatus.FAILED
    assert gates["J6"].value == "no data"
    assert statuses["G8"] is GateStatus.NOT_APPLICABLE
    assert statuses["G9"] is GateStatus.FAILED
    empty = _by_gate([])
    assert empty["G9"].status is GateStatus.FAILED
    assert empty["G9"].value == "no data"


@pytest.mark.unit
def test_metrics_count_runs_and_ignore_non_ok_calls_in_the_ok_shares() -> None:
    metrics = compute_metrics(
        [
            _record(stem_leak=True, check_ok=True),
            _record(outcome=RunOutcome.NOT_OK_SAFETY),
            _record(outcome=RunOutcome.INVALID_OUTPUT),
            _record(outcome=RunOutcome.UNAVAILABLE),
        ]
    )
    assert metrics == GateMetrics(
        runs=4,
        ok_runs=1,
        unavailable_runs=1,
        stem_leak_runs=1,
        register_runs=0,
        ladder_length_share=1.0,
        ladder_similar_share=1.0,
        check_ok_rate=1.0,
        edit_ratio_share=0.0,
        unusable_share=0.6667,
        judge=JudgeMetrics(
            judged_calls=0,
            failed_calls=0,
            meaning_kept_share=None,
            not_invented_share=None,
            goal_done_share=None,
            rule_done_share=None,
            ladder_ok_share=None,
            closing_ask_with_rule=None,
            closing_ask_without_rule=None,
            flagged=(),
        ),
    )


@pytest.mark.unit
def test_baseline_file_holds_rates_only_and_round_trips(tmp_path: Path) -> None:
    metrics = compute_metrics([*_clean(3), _record(check_ok=False)])
    path = tmp_path / "nested" / "baseline.json"
    write_baseline(path, metrics)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload == baseline_payload(metrics)
    assert all(isinstance(value, int | float) or value is None for value in payload.values())
    assert read_baseline(path) == Baseline(check_ok_rate=0.75)
    assert payload["version"] == 3
    assert not {"profanity_runs", "harsh_runs", "hedge_share"} & set(payload)
    assert payload["unusable_share"] == 0.0
    assert payload["unavailable_runs"] == 0
    assert "invalid_share" not in payload


@pytest.mark.unit
def test_baseline_cannot_be_written_without_check_data_or_read_in_a_bad_shape(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="no check_ok data"):
        write_baseline(tmp_path / "b.json", compute_metrics([_record()]))
    bad = tmp_path / "bad.json"
    for content in (
        "[]",
        '{"version": 2, "check_ok_rate": 0.5}',
        '{"version": 1, "check_ok_rate": 0.5}',
        '{"version": 3}',
    ):
        bad.write_text(content, encoding="utf-8")
        with pytest.raises(ValueError, match="baseline"):
            read_baseline(bad)
    bad.write_text('{"version": 3, "check_ok_rate": 2}', encoding="utf-8")
    with pytest.raises(ValueError, match="check_ok_rate"):
        read_baseline(bad)
    bad.write_text('{"version": 3, "check_ok_rate": true}', encoding="utf-8")
    with pytest.raises(ValueError, match="check_ok_rate"):
        read_baseline(bad)


@pytest.mark.unit
def test_report_has_a_gates_section_and_group_summaries() -> None:
    result = RewriteEvalResult(records=[*_clean(3), _record(softer=(PolicyRule.REGISTER,))])
    report = render_report([ModelEval("m", result, 4)], baseline=_BASELINE)
    assert "### Gates" in report
    assert "Denominators (ADR-0016 §10)" in report
    assert "- unavailable calls (in no denominator): 0" in report
    assert "| G4 | ty/vy register change | 1 of 4 calls | 0 | FAIL |" in report
    assert (
        "| J1 | meaning kept | no data | >= 95.0 %; needs a passed judge calibration | n/a |"
        in report
    )
    assert "| G8 | " in report
    assert "### Per category" in report
    assert "| base | 4 | 4 | 0 | 1 | 0 |" in report
    assert "### Per goal" in report
    assert "| none | 4 |" in report
    assert "| register | 0 | 1 | 0 |" in report
    assert "### Closing-ask rule effect" in report


_YES = Verdict.YES
_NO = Verdict.NO
_NA = Verdict.NA


def _slot(
    *,
    clean: Verdict = _YES,
    meaning_kept: Verdict = _YES,
    invented: Verdict = _NO,
    goal_done: Verdict = _NA,
    rule_done: Verdict = _NA,
) -> SlotVerdicts:
    return SlotVerdicts(clean, meaning_kept, invented, goal_done, rule_done)


def _call(
    kind: str,
    index: int,
    *,
    defect: dict[str, Verdict] | None = None,
    ladder_ok: Verdict = _YES,
    closing_ask_with_rule: bool = True,
) -> RewriteRunRecord:
    """A usable call the judge graded; ``defect`` overrides the verdicts of ``softer`` only."""
    goal = RewriteGoal.DECLINE if kind == "goal" else None
    has_rules = kind == "rule"
    good = {
        "goal_done": _YES if goal is not None else _NA,
        "rule_done": _YES if has_rules else _NA,
    }
    return _record(
        check_ok=True,
        goal=goal,
        had_rules=has_rules,
        closing_ask_ok=closing_ask_with_rule if has_rules else False,
        case_id=f"{kind}{index}",
        judgement=RunJudgement(_slot(**{**good, **(defect or {})}), _slot(**good), ladder_ok),
    )


def _judged_pass(
    *,
    bad_kind: str = "plain",
    bad: int = 0,
    defect: dict[str, Verdict] | None = None,
    ladder_bad: int = 0,
    closing_ask_with_rule: bool = True,
) -> list[RewriteRunRecord]:
    """Ten plain, ten goal and ten rule calls; the first ``bad`` of ``bad_kind`` are flawed."""
    return [
        _call(
            kind,
            index,
            defect=defect if kind == bad_kind and index < bad else None,
            ladder_ok=_NO if kind == bad_kind and index < ladder_bad else _YES,
            closing_ask_with_rule=closing_ask_with_rule,
        )
        for kind in ("plain", "goal", "rule")
        for index in range(10)
    ]


@pytest.mark.unit
def test_a_good_judged_pass_makes_every_j_gate_pass() -> None:
    gates = _by_gate(_judged_pass())
    assert [gates[name].status for name in ("J1", "J2", "J3", "J4", "J5", "J6")] == [
        GateStatus.PASSED
    ] * 6
    assert gates["J6"].value == "0 slots"
    assert gates["J4"].value == "rule_done 100.0 %; closing ask 100.0 % vs 0.0 %"
    assert not gates_failed(tuple(gates.values()))


@pytest.mark.unit
@pytest.mark.parametrize(
    ("gate", "bad_kind", "defect", "passing", "failing"),
    [
        ("J1", "plain", {"meaning_kept": _NO}, 3, 4),
        ("J2", "plain", {"invented": _YES}, 3, 4),
        ("J3", "goal", {"goal_done": _NO}, 2, 3),
        ("J4", "rule", {"rule_done": _NO}, 4, 5),
    ],
)
def test_share_gates_sit_exactly_on_their_threshold(
    gate: str, bad_kind: str, defect: dict[str, Verdict], passing: int, failing: int
) -> None:
    at_limit = _by_gate(_judged_pass(bad_kind=bad_kind, bad=passing, defect=defect))
    assert at_limit[gate].status is GateStatus.PASSED
    below = _by_gate(_judged_pass(bad_kind=bad_kind, bad=failing, defect=defect))
    assert below[gate].status is GateStatus.FAILED
    assert gates_failed(tuple(below.values()))


@pytest.mark.unit
def test_j4_also_needs_closing_ask_compliance_with_the_rule_above_without_it() -> None:
    gates = _by_gate(_judged_pass(closing_ask_with_rule=False))
    assert gates["J4"].status is GateStatus.FAILED
    assert gates["J4"].value == "rule_done 100.0 %; closing ask 0.0 % vs 0.0 %"
    no_rule_calls = [_call("rule", index) for index in range(10)]
    assert _by_gate(no_rule_calls)["J4"].value.endswith("vs no data")
    assert _by_gate(no_rule_calls)["J4"].status is GateStatus.FAILED


@pytest.mark.unit
def test_j5_counts_calls_where_the_ladder_holds() -> None:
    assert _by_gate(_judged_pass(ladder_bad=3))["J5"].status is GateStatus.PASSED
    failing = _by_gate(_judged_pass(ladder_bad=4))
    assert failing["J5"].status is GateStatus.FAILED
    assert failing["J5"].value == "86.7 %"


@pytest.mark.unit
def test_j5_ignores_calls_where_the_judge_found_another_defect() -> None:
    defect = {"meaning_kept": _NO}
    records = _judged_pass(bad_kind="plain", bad=10, defect=defect, ladder_bad=10)
    gates = _by_gate(records)
    assert gates["J5"].status is GateStatus.PASSED
    assert gates["J5"].value == "100.0 %"
    assert gates["J1"].status is GateStatus.FAILED


@pytest.mark.unit
def test_j5_counts_the_ladder_only_on_calls_that_are_otherwise_good() -> None:
    # ten plain calls carry a defect and a broken ladder and stay out; of the 25 eligible calls
    # five break the ladder: 20/25.
    records = [
        *_judged_pass(bad_kind="plain", bad=10, defect={"clean": _NO}, ladder_bad=10),
        *(_call("goal", 100 + index, ladder_ok=_NO) for index in range(5)),
    ]
    assert _by_gate(records)["J5"].value == "80.0 %"


@pytest.mark.unit
def test_j6_fails_on_one_flagged_slot_and_lists_it_as_case_run_slot() -> None:
    gates = _by_gate(_judged_pass(bad=1, defect={"clean": _NO}))
    assert gates["J6"].status is GateStatus.FAILED
    assert gates["J6"].value == "1 slots: plain0:1:softer"
    assert gates["J6"].threshold == "0"


@pytest.mark.unit
def test_a_judge_failure_is_never_a_pass() -> None:
    records = [*_judged_pass(), _record(check_ok=True, judge_failed=True)]
    metrics = compute_metrics(records)
    assert (metrics.judge.judged_calls, metrics.judge.failed_calls) == (30, 1)
    gates = _by_gate(records)
    assert gates["J1"].value == "96.8 %"
    assert gates["J5"].value == "100.0 %"
    assert gates["J6"].status is GateStatus.FAILED
    assert gates["J6"].value == "0 slots; 1 judge failures"
    only_failures = _by_gate([_record(check_ok=True, judge_failed=True)])
    assert only_failures["J1"].value == "0.0 %"
    assert only_failures["J6"].status is GateStatus.FAILED


@pytest.mark.unit
def test_unjudged_and_unusable_calls_stay_out_of_the_judge_numbers() -> None:
    records = [*_judged_pass(), *_clean(5), _record(outcome=RunOutcome.REFUSED)]
    metrics = compute_metrics(records)
    assert metrics.judge.judged_calls == 30
    assert metrics.judge.failed_calls == 0
    assert _by_gate(records)["J1"].value == "100.0 %"


@pytest.mark.unit
def test_without_a_calibrated_judge_every_j_gate_is_not_applicable_even_on_defects() -> None:
    records = _judged_pass(bad=5, defect={"clean": _NO, "meaning_kept": _NO})
    gates = _by_gate(records, judge_calibrated=False)
    for name in ("J1", "J2", "J3", "J4", "J5", "J6"):
        assert gates[name].status is GateStatus.NOT_APPLICABLE
        assert gates[name].threshold.endswith("needs a passed judge calibration")
    assert gates["J6"].value.startswith("5 slots: plain0:1:softer")
    assert not gates_failed(tuple(gates.values()))


@pytest.mark.unit
def test_report_lists_every_flagged_slot_with_its_text() -> None:
    flagged = _record(
        check_ok=True,
        case_id="x1",
        run_index=2,
        texts=("Мягко | и вежливо", "Твёрдо"),
        judgement=RunJudgement(_slot(clean=_NO), _slot(), _YES),
    )
    both = _record(
        check_ok=True,
        case_id="x2",
        texts=("Первый", "Второй"),
        judgement=RunJudgement(_slot(clean=_NO), _slot(clean=_NO), _YES),
    )
    result = RewriteEvalResult(records=[flagged, both, *_judged_pass()])
    report = render_report([ModelEval("m", result, 33)], judge_calibrated=True)
    assert "### Judge: slots flagged not clean (J6)" in report
    assert r"| x1:2:softer | Мягко \| и вежливо |" in report
    assert "| x2:1:softer | Первый |" in report
    assert "| x2:1:firmer | Второй |" in report
    assert "x1:2:firmer" not in report.split("### Judge: slots flagged")[1].split("###")[0]
    assert "x1:2:softer, x2:1:softer, x2:1:firmer" in report
    assert "- judge: 32 judged, 0 failed" in report
    assert "- judge calibrated for this model and rubric: yes" in report
    assert "| J6 | no vulgar or rude language (clean = no) |" in report
