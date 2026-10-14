"""Release gates G1-G9 of the rewrite contract (ADR-0016 §10) over one eval pass.

Every number here is a count or a share of calls; no text enters this module, so the
metrics can be written to a committed baseline file.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from svoi_pravila.domain.enums import VariantKind
from svoi_pravila.domain.variant_policy import PolicyRule
from svoi_pravila.evals.rewrite_run import RewriteRunRecord, RunOutcome

G5_MAX_HEDGE_SHARE = 0.05
G6_MIN_LADDER_LENGTH_SHARE = 0.90
G7_MIN_LADDER_SIMILAR_SHARE = 0.95
G8_MAX_EDIT_RATIO_SHARE = 0.05
G9_MAX_INVALID_SHARE = 0.03

_RATE_DIGITS = 4
_BASELINE_VERSION = 1
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
    profanity_runs: int
    harsh_runs: int
    stem_leak_runs: int
    register_runs: int
    hedge_share: float | None
    ladder_length_share: float | None
    ladder_similar_share: float | None
    check_ok_rate: float | None
    edit_ratio_share: float | None
    invalid_share: float | None


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


def compute_metrics(records: list[RewriteRunRecord]) -> GateMetrics:
    """Reduce the records of one model to the numbers the gates read."""
    ok = [record for record in records if record.outcome is RunOutcome.OK]
    plain = [record for record in ok if not record.had_rules]
    checks = [
        variant.check_ok
        for record in ok
        for variant in record.variants
        if variant.check_ok is not None
    ]
    return GateMetrics(
        runs=len(records),
        ok_runs=len(ok),
        profanity_runs=sum(_has_rule(r, PolicyRule.PROFANITY, _TONED) for r in ok),
        harsh_runs=sum(_has_rule(r, PolicyRule.HARSH, _TONED) for r in ok),
        stem_leak_runs=sum(
            any(variant.stem_leak for variant in record.variants if variant.kind in _TONED)
            for record in ok
        ),
        register_runs=sum(_has_rule(r, PolicyRule.REGISTER, tuple(VariantKind)) for r in ok),
        hedge_share=_share(
            sum(_has_rule(r, PolicyRule.HEDGE, (VariantKind.FIRMER,)) for r in plain), len(plain)
        ),
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
        invalid_share=_share(
            sum(record.outcome is RunOutcome.INVALID_OUTPUT for record in records), len(records)
        ),
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


def evaluate_gates(metrics: GateMetrics, baseline: Baseline | None) -> tuple[GateResult, ...]:
    """Evaluate G1-G9; a gate without data fails, G8 without a baseline is ``n/a``."""
    return (
        _zero_gate("G1", "profanity in softer/firmer", metrics.profanity_runs, metrics),
        _zero_gate("G2", "harsh markers in softer/firmer", metrics.harsh_runs, metrics),
        _zero_gate("G3", "forbidden stems in softer/firmer", metrics.stem_leak_runs, metrics),
        _zero_gate("G4", "ty/vy register change", metrics.register_runs, metrics),
        _max_gate("G5", "new hedges in firmer (no rules)", metrics.hedge_share, G5_MAX_HEDGE_SHARE),
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
        _max_gate("G9", "invalid output", metrics.invalid_share, G9_MAX_INVALID_SHARE),
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
        "profanity_runs": metrics.profanity_runs,
        "harsh_runs": metrics.harsh_runs,
        "stem_leak_runs": metrics.stem_leak_runs,
        "register_runs": metrics.register_runs,
        "hedge_share": metrics.hedge_share,
        "ladder_length_share": metrics.ladder_length_share,
        "ladder_similar_share": metrics.ladder_similar_share,
        "check_ok_rate": metrics.check_ok_rate,
        "edit_ratio_share": metrics.edit_ratio_share,
        "invalid_share": metrics.invalid_share,
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
