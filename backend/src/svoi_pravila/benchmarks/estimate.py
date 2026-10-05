"""Offline token-budget estimates for the LLM benchmark (no network)."""

from __future__ import annotations

import math

from svoi_pravila.adapters.llm.gigachat.prepared import (
    prepare_decode,
    prepare_decode_analysis,
    prepare_help_say,
    prepare_soften,
    prepare_suggest_rule,
)
from svoi_pravila.adapters.llm.gigachat.validation import (
    MAX_ANALYSIS_CHARS,
    MAX_TOKENS_ANALYSIS,
    MAX_TOKENS_DECODE,
    MAX_TOKENS_HELP_SAY,
    MAX_TOKENS_SOFTEN,
    MAX_TOKENS_SUGGEST,
)
from svoi_pravila.benchmarks.cases import BenchCase, RunOperation, cases_for_operation


def chars_to_tokens(text: str) -> int:
    """Heuristic: ceil(characters / 3), documented in CLI help and README."""
    if not text:
        return 0
    return math.ceil(len(text) / 3)


def estimate_prepared_tokens(system: str, user: str, *, output_cap: int) -> int:
    """Estimate billable tokens for one provider call from rendered messages."""
    return chars_to_tokens(system) + chars_to_tokens(user) + output_cap


def estimate_case_tokens(case: BenchCase, operation: RunOperation) -> int:
    """Worst-case tokens for one measured case (including one retry per phase)."""
    if operation == "soften" and case.soften is not None:
        prepared = prepare_soften(case.soften)
        one = estimate_prepared_tokens(prepared.system, prepared.user, output_cap=MAX_TOKENS_SOFTEN)
        return 2 * one
    if operation == "help_say" and case.help_say is not None:
        prepared = prepare_help_say(case.help_say)
        one = estimate_prepared_tokens(
            prepared.system, prepared.user, output_cap=MAX_TOKENS_HELP_SAY
        )
        return 2 * one
    if operation == "decode_stream" and case.decode is not None:
        analysis = prepare_decode_analysis(case.decode)
        phase_a = estimate_prepared_tokens(
            analysis.system, analysis.user, output_cap=MAX_TOKENS_ANALYSIS
        )
        analysis_placeholder = "x"
        structured = prepare_decode(case.decode, analysis=analysis_placeholder * MAX_ANALYSIS_CHARS)
        phase_b = estimate_prepared_tokens(
            structured.system, structured.user, output_cap=MAX_TOKENS_DECODE
        )
        return 2 * phase_a + 2 * phase_b
    if operation == "suggest_rule" and case.suggest_rule is not None:
        prepared = prepare_suggest_rule(case.suggest_rule)
        one = estimate_prepared_tokens(
            prepared.system, prepared.user, output_cap=MAX_TOKENS_SUGGEST
        )
        return 2 * one
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
