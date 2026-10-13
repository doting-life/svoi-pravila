"""Markdown report of a rewrite eval pass, written to a local path outside the repository."""

from __future__ import annotations

from collections import Counter

from svoi_pravila.benchmarks.report import percentile
from svoi_pravila.domain.variant_policy import PolicyRule
from svoi_pravila.evals.rewrite_run import RewriteEvalResult, RewriteRunRecord, RunOutcome


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def _summary(result: RewriteEvalResult, spent_billable: int) -> list[str]:
    records = result.records
    latencies = [float(r.latency_ms) for r in records if r.latency_ms is not None]
    outcomes = Counter(record.outcome for record in records)
    by_rule = Counter(
        rule for record in records for variant in record.variants for rule in variant.violations
    )
    checks = [v.check_ok for r in records for v in r.variants if v.check_ok is not None]
    p50 = percentile(latencies, 50)
    p95 = percentile(latencies, 95)
    return [
        "## Summary",
        "",
        f"- runs: {len(records)}",
        *(f"- {outcome.value}: {outcomes.get(outcome, 0)}" for outcome in RunOutcome),
        f"- check_ok: {sum(checks)} / {len(checks)}",
        f"- latency p50 / p95 ms: {p50:.0f} / {p95:.0f}",
        f"- billable tokens: {spent_billable}",
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
        "## Provider refusals per case",
        "",
        "| case | refusals |",
        "|---|---:|",
        *(f"| {case_id} | {refused.get(case_id, 0)} |" for case_id in case_ids),
        "",
    ]


def _invalid_diagnostics(records: list[RewriteRunRecord]) -> list[str]:
    lines = [
        "## Invalid output diagnostics",
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
        "## Case x kind",
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


def render_report(result: RewriteEvalResult, *, model: str, spent_billable: int) -> str:
    """Render the local report; an early stop is marked ``INCOMPLETE`` with its reason."""
    status = "COMPLETE" if result.stop_reason is None else f"INCOMPLETE ({result.stop_reason})"
    lines = [
        "# Rewrite eval",
        "",
        f"- model: {model}",
        f"- status: {status}",
        "",
        *_summary(result, spent_billable),
        *_refusals(result.records),
        *_invalid_diagnostics(result.records),
        *_details(result.records),
    ]
    return "\n".join(lines)
