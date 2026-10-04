"""Token estimates for the safety eval (reuse benchmark heuristics)."""

from __future__ import annotations

from collections.abc import Sequence

from svoi_pravila.application.crisis_screen import CrisisScreen
from svoi_pravila.benchmarks.cases import BenchCase, ExpectedSafety
from svoi_pravila.benchmarks.estimate import estimate_case_tokens
from svoi_pravila.evals.cases import EvalCase, RunOperation


def to_bench_case(case: EvalCase) -> BenchCase:
    """Adapt an eval case to the benchmark estimator (expected_safety is unused)."""
    expected: ExpectedSafety = "crisis" if case.expected == "crisis" else "ok"
    jsonl_op = "decode" if case.operation == "decode_stream" else case.operation
    return BenchCase(
        id=case.id,
        operation=jsonl_op,
        expected_safety=expected,
        smoke=case.smoke,
        soften=case.soften,
        help_say=case.help_say,
        decode=case.decode,
    )


def estimate_eval_case_tokens(case: EvalCase, screen: CrisisScreen) -> int:
    """Zero when the crisis screen would skip the provider; otherwise bench estimate."""
    if screen.hit(case.screen_text()):
        return 0
    return estimate_case_tokens(to_bench_case(case), case.operation)


def largest_eval_call_estimate(cases: list[EvalCase], screen: CrisisScreen) -> int:
    """Worst-case tokens of the single most expensive eval case (0 if all screened)."""
    if not cases:
        return 0
    return max(estimate_eval_case_tokens(case, screen) for case in cases)


def plan_eval_calls(
    cases: list[EvalCase],
    *,
    operations: tuple[RunOperation, ...],
    models: Sequence[str],
    screen: CrisisScreen,
) -> list[tuple[str, RunOperation, int, int]]:
    """Plan rows using unique models as a list aligned with operations.

    ``models`` is one model per selected operation (same length as ``operations``).
    """
    if len(models) != len(operations):
        msg = "models must align 1:1 with operations"
        raise ValueError(msg)
    rows: list[tuple[str, RunOperation, int, int]] = []
    warmed: set[str] = set()
    for model, operation in zip(models, operations, strict=True):
        op_cases = [case for case in cases if case.operation == operation]
        if not op_cases:
            continue
        measured = sum(estimate_eval_case_tokens(case, screen) for case in op_cases)
        warmup = 0
        if model not in warmed:
            live = next(
                (case for case in op_cases if estimate_eval_case_tokens(case, screen) > 0),
                None,
            )
            if live is not None:
                warmup = estimate_eval_case_tokens(live, screen)
                warmed.add(model)
        call_count = len(op_cases) + (1 if warmup else 0)
        rows.append((model, operation, call_count, measured + warmup))
    return rows
