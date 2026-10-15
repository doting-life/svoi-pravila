"""Markdown report of a rewrite eval pass, written to a local path outside the repository."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable

from svoi_pravila.application.ports.rewrite_judge import Verdict
from svoi_pravila.benchmarks.report import percentile
from svoi_pravila.domain.enums import VariantKind
from svoi_pravila.domain.variant_policy import PolicyRule
from svoi_pravila.evals.rewrite_gates import (
    Baseline,
    compute_metrics,
    evaluate_gates,
)
from svoi_pravila.evals.rewrite_run import ModelEval, RewriteRunRecord, RunOutcome


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def _summary(item: ModelEval) -> list[str]:
    records = item.result.records
    latencies = [float(r.latency_ms) for r in records if r.latency_ms is not None]
    outcomes = Counter(record.outcome for record in records)
    by_rule = Counter(
        (variant.kind, rule)
        for record in records
        for variant in record.variants
        for rule in variant.violations
    )
    checks = [v.check_ok for r in records for v in r.variants if v.check_ok is not None]
    p50 = percentile(latencies, 50)
    p95 = percentile(latencies, 95)
    return [
        "### Summary",
        "",
        f"- runs: {len(records)}",
        *(f"- {outcome.value}: {outcomes.get(outcome, 0)}" for outcome in RunOutcome),
        f"- check_ok: {sum(checks)} / {len(checks)}",
        f"- check_empty (draft substituted): {sum(r.check_empty for r in records)}",
        f"- latency p50 / p95 ms: {p50:.0f} / {p95:.0f}",
        f"- billable tokens: {item.spent_billable}",
        "",
        "| policy rule | check | softer | firmer |",
        "|---|---:|---:|---:|",
        *(
            f"| {rule.value} | "
            + " | ".join(str(by_rule.get((kind, rule), 0)) for kind in VariantKind)
            + " |"
            for rule in PolicyRule
        ),
        "",
    ]


def _group_table(title: str, label: str, groups: dict[str, list[RewriteRunRecord]]) -> list[str]:
    lines = [
        f"### {title}",
        "",
        f"| {label} | runs | ok | invalid_output | runs with a violation | stem leaks |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for name, records in groups.items():
        violating = sum(any(v.violations for v in r.variants) for r in records)
        leaks = sum(bool(v.stem_leak) for r in records for v in r.variants)
        ok = sum(r.outcome is RunOutcome.OK for r in records)
        invalid = sum(r.outcome is RunOutcome.INVALID_OUTPUT for r in records)
        lines.append(f"| {name} | {len(records)} | {ok} | {invalid} | {violating} | {leaks} |")
    lines.append("")
    return lines


def _grouped(
    records: list[RewriteRunRecord], key: Callable[[RewriteRunRecord], str]
) -> dict[str, list[RewriteRunRecord]]:
    groups: dict[str, list[RewriteRunRecord]] = {}
    for record in records:
        groups.setdefault(key(record), []).append(record)
    return groups


def _closing_ask_rate(records: list[RewriteRunRecord]) -> str:
    flags = [
        v.closing_ask_ok
        for r in records
        if r.outcome is RunOutcome.OK
        for v in r.variants
        if v.closing_ask_ok is not None
    ]
    return f"{sum(flags)} / {len(flags)}"


def _effect(records: list[RewriteRunRecord]) -> list[str]:
    return [
        "### Closing-ask rule effect",
        "",
        f"- with the rule: {_closing_ask_rate([r for r in records if r.had_rules])}",
        f"- without the rule: {_closing_ask_rate([r for r in records if not r.had_rules])}",
        "",
    ]


def _gates(item: ModelEval, baseline: Baseline | None, *, judge_calibrated: bool) -> list[str]:
    metrics = compute_metrics(item.result.records)
    results = evaluate_gates(metrics, baseline, judge_calibrated=judge_calibrated)
    return [
        "### Gates",
        "",
        "Denominators (ADR-0016 §10): G3, G4, G6, G7, G8 edit_ratio: usable calls (outcome ok); "
        "G8 check_ok: check variants with a reference; G9: all calls except unavailable; "
        "J6: usable calls the judge was asked about (a judge failure is never a pass).",
        "",
        f"- usable calls: {metrics.ok_runs} of {metrics.runs}",
        f"- unavailable calls (in no denominator): {metrics.unavailable_runs}",
        f"- judge: {metrics.judge.judged_calls} judged, {metrics.judge.failed_calls} failed",
        f"- judge calibrated for this model and rubric: {'yes' if judge_calibrated else 'no'}",
        "",
        "| gate | check | measured | threshold | status |",
        "|---|---|---|---|---|",
        *(
            f"| {r.gate} | {r.name} | {r.value} | {r.threshold} | {r.status.value} |"
            for r in results
        ),
        "",
    ]


def _first_attempt_defect(record: RewriteRunRecord) -> str | None:
    """``reason[:slot]`` of the call's first-attempt defect, if it had one.

    A repaired call carries it in ``repaired``; a call that failed on both attempts in the
    first diagnostic. A call that ended unavailable or refused has none recorded.
    """
    if record.repaired:
        return record.repaired[0]
    if record.diagnostics:
        return record.diagnostics[0].reason
    return None


def _repair(records: list[RewriteRunRecord]) -> list[str]:
    """First-attempt defect rate per reason and slot, and the repair success rate."""
    counted = [r for r in records if r.outcome is not RunOutcome.UNAVAILABLE]
    defects = Counter(label for r in counted if (label := _first_attempt_defect(r)) is not None)
    total = sum(defects.values())
    repaired = sum(1 for r in counted if r.repaired)
    success = f"{repaired} / {total}" if total else "n/a"
    return [
        "### Corrective retry",
        "",
        "Denominators: the defect rate is over all calls except unavailable (as G9); the repair "
        "success rate is over the calls whose first attempt had a defect. G9 still counts the "
        "calls that failed after the repair.",
        "",
        f"- calls with a first-attempt defect: {total} of {len(counted)}",
        f"- repaired by the second attempt (repair success rate): {success}",
        "",
        "| first-attempt defect (reason[:slot]) | calls | rate |",
        "|---|---:|---:|",
        *(
            f"| {label} | {count} | {count / len(counted):.1%} |"
            for label, count in sorted(defects.items())
        ),
        "",
    ]


def _flagged(records: list[RewriteRunRecord]) -> list[str]:
    """J6 listing: every slot the judge flagged as not clean, with its text."""
    lines = [
        "### Judge: slots flagged not clean (J6)",
        "",
        "| slot | text |",
        "|---|---|",
    ]
    for record in records:
        if record.judgement is None:
            continue
        verdicts: dict[VariantKind, Verdict] = {
            VariantKind.SOFTER: record.judgement.softer_clean,
            VariantKind.FIRMER: record.judgement.firmer_clean,
        }
        texts = {variant.kind: variant.text for variant in record.variants}
        lines.extend(
            f"| {record.case_id}:{record.run_index}:{kind.value} | {_cell(texts[kind])} |"
            for kind, verdict in verdicts.items()
            if verdict is Verdict.NO
        )
    lines.append("")
    return lines


def _refusals(records: list[RewriteRunRecord]) -> list[str]:
    refused = Counter(r.case_id for r in records if r.outcome is RunOutcome.REFUSED)
    case_ids = list(dict.fromkeys(r.case_id for r in records))
    return [
        "### Provider refusals per case",
        "",
        "| case | refusals |",
        "|---|---:|",
        *(f"| {case_id} | {refused.get(case_id, 0)} |" for case_id in case_ids),
        "",
    ]


def _invalid_diagnostics(records: list[RewriteRunRecord]) -> list[str]:
    lines = [
        "### Invalid output diagnostics",
        "",
        "| case | run | attempt | reason[:slot] | finish_reason |",
        "|---|---:|---:|---|---|",
    ]
    for record in records:
        lines.extend(
            f"| {record.case_id} | {record.run_index} | {attempt} | {item.reason} "
            f"| {item.finish_reason or 'none'} |"
            for attempt, item in enumerate(record.diagnostics, start=1)
        )
    lines.append("")
    return lines


def _check_ok_cell(*, check_ok: bool | None) -> str:
    if check_ok is None:
        return ""
    return "yes" if check_ok else "no"


def _details(records: list[RewriteRunRecord]) -> list[str]:
    lines = [
        "### Case x kind",
        "",
        "| case | run | outcome | kind | violations | check_ok | latency ms | text |",
        "|---|---:|---|---|---|---|---:|---|",
    ]
    for record in records:
        latency = "" if record.latency_ms is None else str(record.latency_ms)
        head = f"| {record.case_id} | {record.run_index} | {record.outcome.value} |"
        if not record.variants:
            lines.append(f"{head} | | | {latency} | |")
        lines.extend(
            f"{head} {variant.kind.value} | {','.join(rule.value for rule in variant.violations)} "
            f"| {_check_ok_cell(check_ok=variant.check_ok)} | {latency} | {_cell(variant.text)} |"
            for variant in record.variants
        )
    lines.append("")
    return lines


def _status(item: ModelEval) -> str:
    reason = item.result.stop_reason
    return "COMPLETE" if reason is None else f"INCOMPLETE ({reason})"


def _comparison(evals: list[ModelEval]) -> list[str]:
    lines = [
        "## Model comparison",
        "",
        "| model | status | runs | invalid_output | policy violations | check_ok "
        "| check_empty | latency p50 / p95 ms | billable tokens |",
        "|---|---|---:|---:|---:|---|---:|---|---:|",
    ]
    for item in evals:
        records = item.result.records
        latencies = [float(r.latency_ms) for r in records if r.latency_ms is not None]
        invalid = sum(1 for r in records if r.outcome is RunOutcome.INVALID_OUTPUT)
        violations = sum(len(v.violations) for r in records for v in r.variants)
        checks = [v.check_ok for r in records for v in r.variants if v.check_ok is not None]
        lines.append(
            f"| {item.model} | {_status(item)} | {len(records)} | {invalid} | {violations} "
            f"| {sum(checks)} / {len(checks)} | {sum(r.check_empty for r in records)} "
            f"| {percentile(latencies, 50):.0f} / {percentile(latencies, 95):.0f} "
            f"| {item.spent_billable} |"
        )
    lines.append("")
    return lines


def render_report(
    evals: list[ModelEval],
    *,
    not_run: tuple[str, ...] = (),
    baseline: Baseline | None = None,
    judge_calibrated: bool = False,
) -> str:
    """Render the local report: a model comparison, then one section per model.

    An early stop marks the model ``INCOMPLETE`` with its reason; models after it are
    listed as not run. Each model section lists the gates G3, G4, G6-G9 and J6, then the
    slots flagged by J6.
    """
    lines = ["# Rewrite eval", "", *_comparison(evals)]
    if not_run:
        lines.extend([f"- not run: {', '.join(not_run)}", ""])
    for item in evals:
        records = item.result.records
        lines.extend(
            [
                f"## Model: {item.model}",
                "",
                f"- status: {_status(item)}",
                "",
                *_summary(item),
                *_group_table(
                    "Per category", "category", _grouped(records, lambda r: r.category.value)
                ),
                *_group_table(
                    "Per goal",
                    "goal",
                    _grouped(records, lambda r: "none" if r.goal is None else r.goal.value),
                ),
                *_effect(records),
                *_gates(item, baseline, judge_calibrated=judge_calibrated),
                *_repair(records),
                *_flagged(records),
                *_refusals(records),
                *_invalid_diagnostics(records),
                *_details(records),
            ]
        )
    return "\n".join(lines)
