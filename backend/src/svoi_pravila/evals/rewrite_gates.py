"""Release gates G3, G4, G6-G9 and J1-J6 of the rewrite contract (ADR-0016 §10, §13).

Every number here is a count or a share of calls; no text enters this module, so the
metrics can be written to a committed baseline file. The flagged slots of J6 are ids
(``case:run:slot``), never text.

Denominators (ADR-0016 §10). A usable call has outcome ``ok``: safety ``ok`` and all three
variants returned. G3, G4, G6, G7 and the G8 ``edit_ratio`` share are measured over usable
calls; the G8 ``check_ok`` rate over the ``check`` variants that have a reference text. G9 is
the share of unusable answers (``invalid_output``, ``not_ok_safety``, ``refused``) over all
calls except ``unavailable``. ``unavailable`` calls (provider failures) leave every denominator
and are reported as a count.

The judge gates J1-J6 are measured over the usable calls the judge was asked about. A judge
failure (no verdicts) stays in every denominator and never counts as a pass; J6 fails while
any judge call failed, because its slots are unverified. J1-J6 are ``n/a`` unless the judge
is calibrated (a passed calibration file for the model and the rubric version).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path

from svoi_pravila.application.ports.rewrite_judge import SlotVerdicts, Verdict
from svoi_pravila.domain.enums import VariantKind
from svoi_pravila.domain.variant_policy import PolicyRule
from svoi_pravila.evals.rewrite_run import RewriteRunRecord, RunOutcome

G6_MIN_LADDER_LENGTH_SHARE = 0.90
G7_MIN_LADDER_SIMILAR_SHARE = 0.95
G8_MAX_EDIT_RATIO_SHARE = 0.05
G9_MAX_UNUSABLE_SHARE = 0.03
J1_MIN_MEANING_KEPT_SHARE = 0.95
J2_MIN_NOT_INVENTED_SHARE = 0.95
J3_MIN_GOAL_DONE_SHARE = 0.90
J4_MIN_RULE_DONE_SHARE = 0.80
J5_MIN_LADDER_OK_SHARE = 0.90

_RATE_DIGITS = 4
_BASELINE_VERSION = 3
_UNUSABLE = (RunOutcome.INVALID_OUTPUT, RunOutcome.NOT_OK_SAFETY, RunOutcome.REFUSED)
_TONED = (VariantKind.SOFTER, VariantKind.FIRMER)


class GateStatus(StrEnum):
    """Verdict of one gate; ``n/a`` when its reference is not available."""

    PASSED = "PASS"
    FAILED = "FAIL"
    NOT_APPLICABLE = "n/a"


@dataclass(frozen=True, slots=True)
class GateMetrics:
    """Counts and shares of one pass. A share is ``None`` when it has no denominator."""

    runs: int
    ok_runs: int
    unavailable_runs: int
    stem_leak_runs: int
    register_runs: int
    ladder_length_share: float | None
    ladder_similar_share: float | None
    check_ok_rate: float | None
    edit_ratio_share: float | None
    unusable_share: float | None
    judge: JudgeMetrics


@dataclass(frozen=True, slots=True)
class JudgeMetrics:
    """The judge's numbers over one pass; shares are ``None`` without a denominator.

    ``flagged`` lists the ``case:run:slot`` ids whose ``clean`` verdict is ``no``.
    """

    judged_calls: int
    failed_calls: int
    meaning_kept_share: float | None
    not_invented_share: float | None
    goal_done_share: float | None
    rule_done_share: float | None
    ladder_ok_share: float | None
    closing_ask_with_rule: float | None
    closing_ask_without_rule: float | None
    flagged: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Baseline:
    """The reference number of an earlier pass that G8 compares against."""

    check_ok_rate: float


@dataclass(frozen=True, slots=True)
class GateResult:
    """One gate row of the report: the measured value, the threshold and the verdict."""

    gate: str
    name: str
    value: str
    threshold: str
    status: GateStatus


def _share(part: int, whole: int) -> float | None:
    return None if whole == 0 else round(part / whole, _RATE_DIGITS)


def _has_rule(record: RewriteRunRecord, rule: PolicyRule, kinds: tuple[VariantKind, ...]) -> bool:
    return any(rule in variant.violations for variant in record.variants if variant.kind in kinds)


def _slots(record: RewriteRunRecord) -> tuple[tuple[str, SlotVerdicts], ...]:
    """The judged slots of one call, named as in the gate table; empty when not judged."""
    if record.judgement is None:
        return ()
    return (
        (VariantKind.SOFTER.value, record.judgement.softer),
        (VariantKind.FIRMER.value, record.judgement.firmer),
    )


def _closing_ask_rate(records: list[RewriteRunRecord]) -> float | None:
    flags = [
        variant.closing_ask_ok
        for record in records
        for variant in record.variants
        if variant.closing_ask_ok is not None
    ]
    return _share(sum(flags), len(flags))


def _judge_metrics(ok: list[RewriteRunRecord]) -> JudgeMetrics:
    asked = [record for record in ok if record.judgement is not None or record.judge_failed]
    judged = [record for record in asked if record.judgement is not None]
    verdicts = [verdict for record in judged for _, verdict in _slots(record)]
    goal_asked = [record for record in asked if record.goal is not None]
    rule_asked = [record for record in asked if record.had_rules]
    goal_verdicts = [verdict for record in goal_asked for _, verdict in _slots(record)]
    rule_verdicts = [verdict for record in rule_asked for _, verdict in _slots(record)]
    return JudgeMetrics(
        judged_calls=len(judged),
        failed_calls=len(asked) - len(judged),
        meaning_kept_share=_share(
            sum(v.meaning_kept is Verdict.YES for v in verdicts), 2 * len(asked)
        ),
        not_invented_share=_share(sum(v.invented is Verdict.NO for v in verdicts), 2 * len(asked)),
        goal_done_share=_share(
            sum(v.goal_done is Verdict.YES for v in goal_verdicts), 2 * len(goal_asked)
        ),
        rule_done_share=_share(
            sum(v.rule_done is Verdict.YES for v in rule_verdicts), 2 * len(rule_asked)
        ),
        ladder_ok_share=_share(
            sum(
                record.judgement is not None and record.judgement.ladder_ok is Verdict.YES
                for record in asked
            ),
            len(asked),
        ),
        closing_ask_with_rule=_closing_ask_rate([r for r in ok if r.had_rules]),
        closing_ask_without_rule=_closing_ask_rate([r for r in ok if not r.had_rules]),
        flagged=tuple(
            f"{record.case_id}:{record.run_index}:{slot}"
            for record in judged
            for slot, verdict in _slots(record)
            if verdict.clean is Verdict.NO
        ),
    )


def compute_metrics(records: list[RewriteRunRecord]) -> GateMetrics:
    """Reduce the records of one model to the numbers the gates read."""
    ok = [record for record in records if record.outcome is RunOutcome.OK]
    unavailable = sum(record.outcome is RunOutcome.UNAVAILABLE for record in records)
    unusable = sum(record.outcome in _UNUSABLE for record in records)
    checks = [
        variant.check_ok
        for record in ok
        for variant in record.variants
        if variant.check_ok is not None
    ]
    return GateMetrics(
        runs=len(records),
        ok_runs=len(ok),
        unavailable_runs=unavailable,
        stem_leak_runs=sum(
            any(variant.stem_leak for variant in record.variants if variant.kind in _TONED)
            for record in ok
        ),
        register_runs=sum(_has_rule(r, PolicyRule.REGISTER, tuple(VariantKind)) for r in ok),
        ladder_length_share=_share(
            sum(not _has_rule(r, PolicyRule.LADDER_LENGTH, (VariantKind.FIRMER,)) for r in ok),
            len(ok),
        ),
        ladder_similar_share=_share(
            sum(not _has_rule(r, PolicyRule.LADDER_SIMILAR, (VariantKind.FIRMER,)) for r in ok),
            len(ok),
        ),
        check_ok_rate=_share(sum(checks), len(checks)),
        edit_ratio_share=_share(
            sum(_has_rule(r, PolicyRule.EDIT_RATIO, (VariantKind.CHECK,)) for r in ok), len(ok)
        ),
        unusable_share=_share(unusable, len(records) - unavailable),
        judge=_judge_metrics(ok),
    )


def _percent(value: float | None) -> str:
    return "no data" if value is None else f"{value * 100:.1f} %"


def _status(*, passed: bool) -> GateStatus:
    return GateStatus.PASSED if passed else GateStatus.FAILED


def _zero_gate(gate: str, name: str, count: int, metrics: GateMetrics) -> GateResult:
    passed = metrics.ok_runs > 0 and count == 0
    value = "no data" if metrics.ok_runs == 0 else f"{count} of {metrics.ok_runs} calls"
    return GateResult(gate, name, value, "0", _status(passed=passed))


def _max_gate(gate: str, name: str, value: float | None, limit: float) -> GateResult:
    passed = value is not None and value <= limit
    return GateResult(gate, name, _percent(value), f"<= {_percent(limit)}", _status(passed=passed))


def _min_gate(gate: str, name: str, value: float | None, limit: float) -> GateResult:
    passed = value is not None and value >= limit
    return GateResult(gate, name, _percent(value), f">= {_percent(limit)}", _status(passed=passed))


def _baseline_gate(metrics: GateMetrics, baseline: Baseline | None) -> GateResult:
    name = "check_ok rate not worse than the baseline; edit_ratio share"
    value = (
        f"check_ok {_percent(metrics.check_ok_rate)}; "
        f"edit_ratio {_percent(metrics.edit_ratio_share)}"
    )
    if baseline is None:
        return GateResult("G8", name, value, "needs --baseline", GateStatus.NOT_APPLICABLE)
    threshold = (
        f"check_ok >= {_percent(baseline.check_ok_rate)}; "
        f"edit_ratio <= {_percent(G8_MAX_EDIT_RATIO_SHARE)}"
    )
    passed = (
        metrics.check_ok_rate is not None
        and metrics.edit_ratio_share is not None
        and metrics.check_ok_rate >= baseline.check_ok_rate
        and metrics.edit_ratio_share <= G8_MAX_EDIT_RATIO_SHARE
    )
    return GateResult("G8", name, value, threshold, _status(passed=passed))


def _not_calibrated(result: GateResult) -> GateResult:
    threshold = f"{result.threshold}; needs a passed judge calibration"
    return replace(result, threshold=threshold, status=GateStatus.NOT_APPLICABLE)


def _closing_ask_gate(metrics: JudgeMetrics) -> GateResult:
    name = "rule_done = yes in slots with rules; closing ask with rule > without"
    with_rule = metrics.closing_ask_with_rule
    without = metrics.closing_ask_without_rule
    value = (
        f"rule_done {_percent(metrics.rule_done_share)}; "
        f"closing ask {_percent(with_rule)} vs {_percent(without)}"
    )
    threshold = f"rule_done >= {_percent(J4_MIN_RULE_DONE_SHARE)}; with > without"
    passed = (
        metrics.rule_done_share is not None
        and with_rule is not None
        and without is not None
        and metrics.rule_done_share >= J4_MIN_RULE_DONE_SHARE
        and with_rule > without
    )
    return GateResult("J4", name, value, threshold, _status(passed=passed))


def _clean_gate(metrics: JudgeMetrics) -> GateResult:
    """J6: ``clean`` = no in no slot; a judge failure leaves its slots unverified."""
    value = f"{len(metrics.flagged)} slots"
    if metrics.flagged:
        value += f": {', '.join(metrics.flagged)}"
    if metrics.failed_calls:
        value += f"; {metrics.failed_calls} judge failures"
    passed = metrics.judged_calls > 0 and not metrics.flagged and metrics.failed_calls == 0
    if metrics.judged_calls == 0 and not metrics.failed_calls:
        value = "no data"
    return GateResult(
        "J6", "no vulgar or rude language (clean = no)", value, "0", _status(passed=passed)
    )


def _judge_gates(metrics: JudgeMetrics) -> tuple[GateResult, ...]:
    return (
        _min_gate("J1", "meaning kept", metrics.meaning_kept_share, J1_MIN_MEANING_KEPT_SHARE),
        _min_gate("J2", "nothing invented", metrics.not_invented_share, J2_MIN_NOT_INVENTED_SHARE),
        _min_gate("J3", "goal done", metrics.goal_done_share, J3_MIN_GOAL_DONE_SHARE),
        _closing_ask_gate(metrics),
        _min_gate("J5", "ladder holds", metrics.ladder_ok_share, J5_MIN_LADDER_OK_SHARE),
        _clean_gate(metrics),
    )


def evaluate_gates(
    metrics: GateMetrics, baseline: Baseline | None, *, judge_calibrated: bool
) -> tuple[GateResult, ...]:
    """Evaluate G3, G4, G6-G9 and J1-J6.

    A gate without data fails; G8 without a baseline and J1-J6 without a calibrated judge
    are ``n/a``.
    """
    judge_gates = _judge_gates(metrics.judge)
    return (
        _zero_gate("G3", "forbidden stems in softer/firmer", metrics.stem_leak_runs, metrics),
        _zero_gate("G4", "ty/vy register change", metrics.register_runs, metrics),
        _min_gate(
            "G6",
            "firmer not longer than softer",
            metrics.ladder_length_share,
            G6_MIN_LADDER_LENGTH_SHARE,
        ),
        _min_gate(
            "G7",
            "softer and firmer differ",
            metrics.ladder_similar_share,
            G7_MIN_LADDER_SIMILAR_SHARE,
        ),
        _baseline_gate(metrics, baseline),
        _max_gate("G9", "unusable answers", metrics.unusable_share, G9_MAX_UNUSABLE_SHARE),
        *(judge_gates if judge_calibrated else tuple(_not_calibrated(g) for g in judge_gates)),
    )


def gates_failed(results: tuple[GateResult, ...]) -> bool:
    """True when any gate is FAIL; ``n/a`` does not fail."""
    return any(result.status is GateStatus.FAILED for result in results)


def baseline_payload(metrics: GateMetrics) -> dict[str, object]:
    """The C0 numbers written by ``--write-baseline``: rates and counts, never text."""
    return {
        "version": _BASELINE_VERSION,
        "runs": metrics.runs,
        "ok_runs": metrics.ok_runs,
        "unavailable_runs": metrics.unavailable_runs,
        "stem_leak_runs": metrics.stem_leak_runs,
        "register_runs": metrics.register_runs,
        "ladder_length_share": metrics.ladder_length_share,
        "ladder_similar_share": metrics.ladder_similar_share,
        "check_ok_rate": metrics.check_ok_rate,
        "edit_ratio_share": metrics.edit_ratio_share,
        "unusable_share": metrics.unusable_share,
    }


def write_baseline(path: Path, metrics: GateMetrics) -> None:
    """Write the baseline file; a pass without ``check`` references cannot serve as one."""
    if metrics.check_ok_rate is None:
        msg = "cannot write a baseline: the pass has no check_ok data"
        raise ValueError(msg)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(baseline_payload(metrics), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _rate(raw: object, field_name: str) -> float:
    if isinstance(raw, bool) or not isinstance(raw, int | float) or not 0 <= raw <= 1:
        msg = f"baseline {field_name} must be a number between 0 and 1"
        raise ValueError(msg)
    return float(raw)


def read_baseline(path: Path) -> Baseline:
    """Read the numbers G8 needs from a baseline file; any other shape is an error."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or raw.get("version") != _BASELINE_VERSION:
        msg = f"{path}: not a version {_BASELINE_VERSION} baseline file"
        raise ValueError(msg)
    return Baseline(check_ok_rate=_rate(raw.get("check_ok_rate"), "check_ok_rate"))
