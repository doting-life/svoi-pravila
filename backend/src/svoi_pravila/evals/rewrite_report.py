"""Markdown report of a rewrite eval pass, written to a local path outside the repository."""

from __future__ import annotations

from collections import Counter

from svoi_pravila.benchmarks.report import percentile
from svoi_pravila.domain.variant_policy import PolicyRule
from svoi_pravila.evals.rewrite_run import ModelEval, RewriteRunRecord, RunOutcome


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def _summary(item: ModelEval) -> list[str]:
    records = item.result.records
    latencies = [float(r.latency_ms) for r in records if r.latency_ms is not None]
    outcomes = Counter(record.outcome for record in records)
    by_rule = Counter(
        rule for record in records for variant in record.variants for rule in variant.violations
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
        "| policy rule | violations |",
        "|---|---:|",
        *(f"| {rule.value} | {by_rule.get(rule, 0)} |" for rule in PolicyRule),
        "",
    ]


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


def render_report(evals: list[ModelEval], *, not_run: tuple[str, ...] = ()) -> str:
    """Render the local report: a model comparison, then one section per model.

    An early stop marks the model ``INCOMPLETE`` with its reason; models after it are
    listed as not run.
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
                *_refusals(records),
                *_invalid_diagnostics(records),
                *_details(records),
            ]
        )
    return "\n".join(lines)
