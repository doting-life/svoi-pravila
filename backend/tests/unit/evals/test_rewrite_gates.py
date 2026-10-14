"""Offline proof of the release gates G1-G9: recorded runs make each gate PASS and FAIL."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from svoi_pravila.domain.enums import VariantKind
from svoi_pravila.domain.variant_policy import PolicyRule
from svoi_pravila.evals.rewrite_cases import RewriteCategory
from svoi_pravila.evals.rewrite_gates import (
    Baseline,
    GateMetrics,
    GateResult,
    GateStatus,
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
    RunOutcome,
    RunVariant,
)


def _variant(
    kind: VariantKind,
    *rules: PolicyRule,
    check_ok: bool | None = None,
    stem_leak: bool | None = None,
) -> RunVariant:
    return RunVariant(kind, "text", rules, check_ok=check_ok, stem_leak=stem_leak)


def _record(
    *,
    check: tuple[PolicyRule, ...] = (),
    softer: tuple[PolicyRule, ...] = (),
    firmer: tuple[PolicyRule, ...] = (),
    check_ok: bool | None = None,
    stem_leak: bool | None = None,
    had_rules: bool = False,
    outcome: RunOutcome = RunOutcome.OK,
) -> RewriteRunRecord:
    variants = (
        ()
        if outcome is not RunOutcome.OK
        else (
            _variant(VariantKind.CHECK, *check, check_ok=check_ok),
            _variant(VariantKind.SOFTER, *softer, stem_leak=stem_leak),
            _variant(VariantKind.FIRMER, *firmer, stem_leak=stem_leak),
        )
    )
    return RewriteRunRecord(
        case_id="c",
        category=RewriteCategory.BASE,
        goal=None,
        had_rules=had_rules,
        run_index=1,
        outcome=outcome,
        variants=variants,
        latency_ms=1,
        billable_tokens=1,
    )


def _clean(count: int) -> list[RewriteRunRecord]:
    return [_record(check_ok=True) for _ in range(count)]


def _by_gate(
    records: list[RewriteRunRecord], baseline: Baseline | None = None
) -> dict[str, GateResult]:
    results = evaluate_gates(compute_metrics(records), baseline)
    return {result.gate: result for result in results}


_BASELINE = Baseline(check_ok_rate=1.0)


@pytest.mark.unit
def test_clean_pass_makes_every_gate_pass_and_g8_needs_a_baseline() -> None:
    gates = _by_gate(_clean(20))
    assert [g.status for g in gates.values()] == [GateStatus.PASSED] * 7 + [
        GateStatus.NOT_APPLICABLE,
        GateStatus.PASSED,
    ]
    assert not gates_failed(tuple(gates.values()))
    with_baseline = _by_gate(_clean(20), _BASELINE)
    assert with_baseline["G8"].status is GateStatus.PASSED


@pytest.mark.unit
@pytest.mark.parametrize(
    ("gate", "rule", "kind"),
    [
        ("G1", PolicyRule.PROFANITY, VariantKind.SOFTER),
        ("G1", PolicyRule.PROFANITY, VariantKind.FIRMER),
        ("G2", PolicyRule.HARSH, VariantKind.SOFTER),
        ("G2", PolicyRule.HARSH, VariantKind.FIRMER),
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
def test_g5_hedge_share_is_over_calls_without_rules_and_limited_to_five_percent() -> None:
    hedged = _record(firmer=(PolicyRule.HEDGE,), check_ok=True)
    assert _by_gate([*_clean(19), hedged])["G5"].status is GateStatus.PASSED
    assert _by_gate([*_clean(18), hedged, hedged])["G5"].status is GateStatus.FAILED
    with_rules = _record(firmer=(PolicyRule.HEDGE,), had_rules=True, check_ok=True)
    ignored = _by_gate([*_clean(20), with_rules])["G5"]
    assert ignored.status is GateStatus.PASSED
    assert ignored.value == "0.0 %"


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
def test_g9_limits_invalid_output_to_three_percent_of_all_calls() -> None:
    invalid = _record(outcome=RunOutcome.INVALID_OUTPUT)
    assert _by_gate([*_clean(99), invalid])["G9"].status is GateStatus.PASSED
    assert (
        _by_gate([*_clean(96), invalid, invalid, invalid, invalid])["G9"].status
        is GateStatus.FAILED
    )


@pytest.mark.unit
def test_a_pass_without_ok_calls_fails_every_gate_that_needs_data() -> None:
    gates = _by_gate([_record(outcome=RunOutcome.REFUSED)])
    statuses = {gate: result.status for gate, result in gates.items()}
    assert statuses["G1"] is statuses["G5"] is statuses["G7"] is GateStatus.FAILED
    assert gates["G1"].value == "no data"
    assert statuses["G8"] is GateStatus.NOT_APPLICABLE
    assert statuses["G9"] is GateStatus.PASSED
    empty = _by_gate([])
    assert empty["G9"].status is GateStatus.FAILED
    assert empty["G9"].value == "no data"


@pytest.mark.unit
def test_metrics_count_runs_and_ignore_non_ok_calls_in_the_ok_shares() -> None:
    metrics = compute_metrics(
        [
            _record(softer=(PolicyRule.PROFANITY,), stem_leak=True, check_ok=True),
            _record(outcome=RunOutcome.NOT_OK_SAFETY),
            _record(outcome=RunOutcome.INVALID_OUTPUT),
        ]
    )
    assert metrics == GateMetrics(
        runs=3,
        ok_runs=1,
        profanity_runs=1,
        harsh_runs=0,
        stem_leak_runs=1,
        register_runs=0,
        hedge_share=0.0,
        ladder_length_share=1.0,
        ladder_similar_share=1.0,
        check_ok_rate=1.0,
        edit_ratio_share=0.0,
        invalid_share=0.3333,
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


@pytest.mark.unit
def test_baseline_cannot_be_written_without_check_data_or_read_in_a_bad_shape(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="no check_ok data"):
        write_baseline(tmp_path / "b.json", compute_metrics([_record()]))
    bad = tmp_path / "bad.json"
    for content in ("[]", '{"version": 2}', '{"version": 1}', '{"version": 1, "check_ok_rate": 2}'):
        bad.write_text(content, encoding="utf-8")
        with pytest.raises(ValueError, match="baseline"):
            read_baseline(bad)
    bad.write_text('{"version": 1, "check_ok_rate": true}', encoding="utf-8")
    with pytest.raises(ValueError, match="check_ok_rate"):
        read_baseline(bad)


@pytest.mark.unit
def test_report_has_a_gates_section_and_group_summaries() -> None:
    result = RewriteEvalResult(records=[*_clean(3), _record(softer=(PolicyRule.PROFANITY,))])
    report = render_report([ModelEval("m", result, 4)], baseline=_BASELINE)
    assert "### Gates" in report
    assert "| G1 | profanity in softer/firmer | 1 of 4 calls | 0 | FAIL |" in report
    assert "| G8 | " in report
    assert "### Per category" in report
    assert "| base | 4 | 4 | 0 | 1 | 0 |" in report
    assert "### Per goal" in report
    assert "| none | 4 |" in report
    assert "| profanity | 0 | 1 | 0 |" in report
    assert "### Closing-ask rule effect" in report
