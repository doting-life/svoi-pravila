"""CLI entry for the safety eval (on demand, never from pytest)."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from svoi_pravila.adapters.llm.gigachat.adapter import GigaChatTextGenerator
from svoi_pravila.adapters.llm.gigachat.client import close_gigachat_client, create_gigachat_client
from svoi_pravila.application.crisis_screen import CrisisScreen
from svoi_pravila.benchmarks.out_writer import OutWriter
from svoi_pravila.benchmarks.recording import RateLimitCapture, install_rate_limit_capture
from svoi_pravila.benchmarks.runner import (
    RateLimitedError,
    SpendTracker,
    TokenBudgetExceededError,
)
from svoi_pravila.config import LlmToolSettings
from svoi_pravila.evals.cases import OPERATIONS, EvalCase, RunOperation, filter_cases, load_cases
from svoi_pravila.evals.estimate import largest_eval_call_estimate, plan_eval_calls
from svoi_pravila.evals.metrics import EvalRecord, format_metrics
from svoi_pravila.evals.runner import (
    EvalParams,
    EvalRuntime,
    eval_exit_code,
    run_eval,
    warmup_eval,
    write_eval_out,
)

_DEFAULT_DATA = "/app/evals/data/eval_v1.jsonl"
_ESTIMATE_HELP = (
    "Token estimate: crisis-screen hits cost 0; otherwise the benchmark heuristic "
    "(ceil(rendered system+user characters / 3) + output cap, with retries)."
)


def _parse_ops(raw: list[str] | None) -> tuple[RunOperation, ...]:
    if raw is None:
        return OPERATIONS
    ordered: list[RunOperation] = []
    for item in raw:
        if item not in OPERATIONS:
            msg = f"unknown operations: {item}"
            raise SystemExit(msg)
        ordered.append(item)
    return tuple(dict.fromkeys(ordered))


def models_for_operations(
    settings: LlmToolSettings, operations: tuple[RunOperation, ...]
) -> list[str]:
    """Configured model per operation (Lightning soften/help_say, 2-Pro decode)."""
    mapping = {
        "soften": settings.gigachat_model_soften,
        "help_say": settings.gigachat_model_help_say,
        "decode_stream": settings.gigachat_model_decode,
    }
    return [mapping[operation] for operation in operations]


def _print_dry_run(
    cases: list[EvalCase],
    *,
    models: list[str],
    operations: tuple[RunOperation, ...],
    screen: CrisisScreen,
) -> None:
    print("Dry-run (no network calls).")
    print(_ESTIMATE_HELP)
    print(f"Operations: {', '.join(operations)}")
    total_calls = 0
    total_tokens = 0
    for model, operation, call_count, tokens in plan_eval_calls(
        cases, operations=operations, models=models, screen=screen
    ):
        print(f"  {model} / {operation}: calls={call_count} (incl. warmup) est_tokens={tokens}")
        total_calls += call_count
        total_tokens += tokens
    print(f"Total planned calls: {total_calls}")
    print(f"Total estimated billable tokens: {total_tokens}")
    largest = largest_eval_call_estimate(cases, screen)
    print(f"Largest single-call worst-case estimate: {largest}")


async def async_main(args: argparse.Namespace) -> int:
    """Run the safety eval and print/write C0 metrics."""
    settings = LlmToolSettings()
    screen = CrisisScreen.load_ru_v2()
    cases = filter_cases(
        load_cases(Path(args.data)),
        smoke=bool(args.smoke),
        case_ids=tuple(args.cases) if args.cases else None,
    )
    operations = _parse_ops(args.ops)
    models = models_for_operations(settings, operations)
    if args.dry_run:
        _print_dry_run(cases, models=models, operations=operations, screen=screen)
        return 0
    if args.max_tokens is None:
        msg = "--max-tokens is required for live runs (or use --dry-run)"
        raise SystemExit(msg)

    client = create_gigachat_client(settings)
    uninstall_rate_limits = None
    rate_limits = RateLimitCapture()
    out = OutWriter(Path(args.out) if args.out is not None else None)
    spend = SpendTracker()
    params = EvalParams(
        operations=operations,
        models=tuple(models),
        deadline=args.deadline,
        show_outputs=args.show_outputs,
        max_tokens=args.max_tokens,
    )
    runtime = EvalRuntime(out=out, spend=spend, screen=screen, rate_limits=rate_limits)
    try:
        uninstall_rate_limits = install_rate_limit_capture(client, rate_limits)
        gen = GigaChatTextGenerator(client, settings)
        print("Calls are sequential. One unmeasured warm-up runs per distinct model.")
        print(f"Operations: {', '.join(operations)}")
        print(f"Max billable tokens: {args.max_tokens}")
        records, incomplete, rate_error, budget_error = await _run_live(
            gen, cases, params, runtime, out
        )
        _write_incomplete(
            out,
            incomplete=incomplete,
            rate_error=rate_error,
            budget_error=budget_error,
        )
        print(format_metrics(records))
        print(
            f"Spent billable tokens: {spend.spent_billable} "
            f"(warmup_billable={spend.warmup_billable})"
        )
        return eval_exit_code(records, incomplete=incomplete)
    finally:
        if uninstall_rate_limits is not None:
            await uninstall_rate_limits()
        await close_gigachat_client(client)


async def _run_live(
    gen: GigaChatTextGenerator,
    cases: list[EvalCase],
    params: EvalParams,
    runtime: EvalRuntime,
    out: OutWriter,
) -> tuple[list[EvalRecord], bool, RateLimitedError | None, TokenBudgetExceededError | None]:
    try:
        await warmup_eval(gen, cases, params, runtime)
        return await run_eval(gen, cases, params, runtime)
    except RateLimitedError as exc:
        write_eval_out(out, [])
        return [], True, exc, None
    except TokenBudgetExceededError as exc:
        write_eval_out(out, [])
        return [], True, None, exc


def _write_incomplete(
    out: OutWriter,
    *,
    incomplete: bool,
    rate_error: RateLimitedError | None,
    budget_error: TokenBudgetExceededError | None,
) -> None:
    if not incomplete:
        return
    if budget_error is not None:
        out.write_incomplete(token_budget=(budget_error.spent, budget_error.limit))
        return
    if rate_error is not None:
        out.write_incomplete(
            http_status=rate_error.http_status,
            rate_limit_headers=rate_error.rate_limit_headers,
        )


def main() -> None:
    """CLI entrypoint for ``svoi-pravila-eval``."""
    parser = argparse.ArgumentParser(description="GigaChat safety eval v1", epilog=_ESTIMATE_HELP)
    parser.add_argument("--data", default=_DEFAULT_DATA, help="Path to JSONL eval dataset")
    parser.add_argument(
        "--ops",
        nargs="*",
        default=None,
        choices=list(OPERATIONS),
        help="Operations to run (default: all)",
    )
    parser.add_argument("--deadline", type=float, default=30.0, help="Per-call deadline seconds")
    parser.add_argument("--out", default=None, help="Write C0 metrics to this path")
    parser.add_argument(
        "--show-outputs",
        action="store_true",
        help="Print generated texts to stderr (never to --out)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned calls and token estimate; make no network calls",
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=None,
        help="Hard billable-token budget (required unless --dry-run)",
    )
    parser.add_argument("--smoke", action="store_true", help="Run only smoke:true cases")
    parser.add_argument("--cases", nargs="+", default=None, help="Restrict to these case ids")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(async_main(args)))


if __name__ == "__main__":
    main()
