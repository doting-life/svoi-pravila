"""Offline token-budget estimates for the LLM benchmark (no network)."""

from __future__ import annotations

from svoi_pravila.adapters.llm.gigachat.estimate import (
    chars_to_tokens,
    estimate_prepared_tokens,
    max_billable_for_request,
)
from svoi_pravila.benchmarks.cases import BenchCase, RunOperation, cases_for_operation

__all__ = [
    "chars_to_tokens",
    "estimate_case_tokens",
    "estimate_prepared_tokens",
    "plan_calls",
]


def estimate_case_tokens(case: BenchCase, operation: RunOperation) -> int:
    """Worst-case tokens for one measured case (including retries per phase)."""
    if operation == "soften" and case.soften is not None:
        return max_billable_for_request(case.soften)
    if operation == "help_say" and case.help_say is not None:
        return max_billable_for_request(case.help_say)
    if operation == "decode_stream" and case.decode is not None:
        return max_billable_for_request(case.decode)
    if operation == "suggest_rule" and case.suggest_rule is not None:
        return max_billable_for_request(case.suggest_rule)
    msg = f"case {case.id} missing request for {operation}"
    raise ValueError(msg)


def plan_calls(
    cases: list[BenchCase],
    *,
    operations: tuple[RunOperation, ...],
    models: list[str],
    repeat: int,
) -> list[tuple[str, RunOperation, int, int]]:
    """Return (model, operation, call_count, estimated_tokens) including warm-up.

    call_count counts warm-up (1) plus measured cases * repeat. Token estimate uses
    worst-case retries (2 attempts per structured phase; 2x2 for decode_stream).
    """
    rows: list[tuple[str, RunOperation, int, int]] = []
    for model in models:
        for operation in operations:
            op_cases = cases_for_operation(cases, operation)
            if not op_cases:
                continue
            measured = len(op_cases) * repeat
            call_count = measured + 1
            tokens = sum(estimate_case_tokens(case, operation) for case in op_cases) * repeat
            tokens += estimate_case_tokens(op_cases[0], operation)
            rows.append((model, operation, call_count, tokens))
    return rows
