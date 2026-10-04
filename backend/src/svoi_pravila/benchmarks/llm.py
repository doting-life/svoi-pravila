"""CLI entry for the GigaChat generation benchmark."""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from pathlib import Path

from gigachat import GigaChat

from svoi_pravila.adapters.llm.gigachat.adapter import GigaChatTextGenerator
from svoi_pravila.adapters.llm.gigachat.client import close_gigachat_client, create_gigachat_client
from svoi_pravila.adapters.llm.gigachat.mapping import PROVIDER_EXCEPTIONS, map_provider_exception
from svoi_pravila.application.ports.generation import TokenUsage
from svoi_pravila.benchmarks.cases import (
    RUN_OPERATIONS,
    BenchCase,
    RunOperation,
    cases_for_operation,
    filter_cases,
    load_cases,
)
from svoi_pravila.benchmarks.estimate import plan_calls
from svoi_pravila.benchmarks.out_writer import OutWriter
from svoi_pravila.benchmarks.recording import (
    RateLimitCapture,
    RecordingConfig,
    install_rate_limit_capture,
    install_recording,
)
from svoi_pravila.benchmarks.report import (
    PRICE_TABLE_DATE,
    aggregate_row,
    decode_stream_report_header,
    default_models,
    format_reasons_line,
    format_report,
    standard_report_header,
)
from svoi_pravila.benchmarks.runner import (
    BenchmarkParams,
    BenchmarkRuntime,
    RateLimitedError,
    SpendTracker,
    TokenBudgetExceededError,
    run_benchmark,
    warmup,
)
from svoi_pravila.config import Settings

_DEFAULT_OPS: tuple[RunOperation, ...] = ("soften", "help_say", "decode_stream")

_ESTIMATE_HELP = (
    "Token estimate heuristic: ceil(rendered system+user characters / 3) "
    "+ per-operation output cap; worst-case retries counted (2x per structured phase)."
)


def _parse_ops(raw: list[str] | None) -> tuple[RunOperation, ...]:
    if raw is None:
        return _DEFAULT_OPS
    ordered: list[RunOperation] = []
    for item in raw:
        if item == "soften":
            ordered.append("soften")
        elif item == "help_say":
            ordered.append("help_say")
        elif item == "decode_stream":
            ordered.append("decode_stream")
        else:
            msg = f"unknown operations: {item}"
            raise SystemExit(msg)
    return tuple(dict.fromkeys(ordered))


async def _print_available_models(client: GigaChat, *, list_only: bool) -> None:
    try:
        models_response = await client.aget_models()
        available = [item.id_ for item in models_response.data]
        print("Available models:")
        for name in available:
            print(f"- {name}")
    except PROVIDER_EXCEPTIONS as exc:
        if list_only:
            raise map_provider_exception(exc, usage=TokenUsage(), attempts=0) from None
        print(
            f"Warning: could not list models ({type(exc).__name__}); "
            "continuing with requested models."
        )


def _print_dry_run(
    cases: list[BenchCase],
    *,
    models: list[str],
    operations: tuple[RunOperation, ...],
    repeat: int,
) -> None:
    print("Dry-run (no network calls).")
    print(_ESTIMATE_HELP)
    print(f"Models: {', '.join(models)}")
    print(f"Operations: {', '.join(operations)}")
    print(f"Repeat: {repeat}")
    total_calls = 0
    total_tokens = 0
    for model, operation, call_count, tokens in plan_calls(
        cases, operations=operations, models=models, repeat=repeat
    ):
        print(f"  {model} / {operation}: calls={call_count} (incl. warmup) est_tokens={tokens}")
        total_calls += call_count
        total_tokens += tokens
    print(f"Total planned calls: {total_calls}")
    print(f"Total estimated billable tokens: {total_tokens}")


@dataclass(frozen=True, slots=True)
class _ModelRun:
    client: GigaChat
    settings: Settings
    cases: list[BenchCase]
    model: str
    params: BenchmarkParams
    out: OutWriter
    spend: SpendTracker
    rate_limits: RateLimitCapture


async def _run_model(
    run: _ModelRun,
) -> tuple[list[str], bool, RateLimitedError | None, TokenBudgetExceededError | None]:
    run_settings = run.settings.model_copy(
        update={
            "gigachat_model_soften": run.model,
            "gigachat_model_help_say": run.model,
            "gigachat_model_decode": run.model,
        }
    )
    gen = GigaChatTextGenerator(run.client, run_settings)
    try:
        await warmup(
            gen,
            run.cases,
            run.params,
            BenchmarkRuntime(run.out, run.spend, run.rate_limits),
        )
    except RateLimitedError as exc:
        operation = next(
            (op for op in run.params.operations if cases_for_operation(run.cases, op)),
            run.params.operations[0],
        )
        header = (
            decode_stream_report_header()
            if operation == "decode_stream"
            else standard_report_header()
        )
        run.out.write_header_once(header)
        row, reasons = aggregate_row([exc.record], operation=operation, model=run.model)
        run.out.write_row(row)
        run.out.write_reasons(format_reasons_line(reasons))
        run.spend.add(exc.record.billable_tokens, warmup=True)
        return [header, row], True, exc, None
    except TokenBudgetExceededError as exc:
        return [], True, None, exc
    model_params = BenchmarkParams(
        operations=run.params.operations,
        model=run.model,
        repeat=run.params.repeat,
        deadline=run.params.deadline,
        show_outputs=run.params.show_outputs,
        max_tokens=run.params.max_tokens,
    )
    return await run_benchmark(
        gen,
        run.cases,
        model_params,
        BenchmarkRuntime(run.out, run.spend, run.rate_limits),
    )


def _load_selected_cases(args: argparse.Namespace) -> list[BenchCase]:
    return filter_cases(
        load_cases(Path(args.data)),
        smoke=bool(args.smoke),
        case_ids=tuple(args.cases) if args.cases else None,
    )


@dataclass(frozen=True, slots=True)
class _LiveRunResult:
    rows: list[str]
    incomplete: bool
    rate_error: RateLimitedError | None
    budget_error: TokenBudgetExceededError | None
    spend: SpendTracker


def _finish_report(out: OutWriter, result: _LiveRunResult) -> int:
    if result.incomplete and result.budget_error is not None:
        out.write_incomplete(token_budget=(result.budget_error.spent, result.budget_error.limit))
    elif result.incomplete and result.rate_error is not None:
        out.write_incomplete(
            http_status=result.rate_error.http_status,
            rate_limit_headers=result.rate_error.rate_limit_headers,
        )
    print(format_report(result.rows, incomplete=result.incomplete))
    print(
        f"Spent billable tokens: {result.spend.spent_billable} "
        f"(warmup_billable={result.spend.warmup_billable})"
    )
    return 1 if result.incomplete else 0


async def _run_live_models(base: _ModelRun, model_list: list[str]) -> _LiveRunResult:
    all_rows: list[str] = []
    for model in model_list:
        model_rows, incomplete, rate_error, budget_error = await _run_model(
            _ModelRun(
                client=base.client,
                settings=base.settings,
                cases=base.cases,
                model=model,
                params=base.params,
                out=base.out,
                spend=base.spend,
                rate_limits=base.rate_limits,
            )
        )
        all_rows.extend(model_rows)
        if incomplete:
            return _LiveRunResult(
                rows=all_rows,
                incomplete=True,
                rate_error=rate_error,
                budget_error=budget_error,
                spend=base.spend,
            )
    return _LiveRunResult(
        rows=all_rows,
        incomplete=False,
        rate_error=None,
        budget_error=None,
        spend=base.spend,
    )


async def async_main(args: argparse.Namespace) -> int:
    """Run the benchmark and print/write C0 markdown tables."""
    settings = Settings()
    cases = _load_selected_cases(args)
    operations = _parse_ops(args.ops)

    if args.dry_run:
        model_list = list(dict.fromkeys(args.models)) if args.models else default_models(settings)
        _print_dry_run(cases, models=model_list, operations=operations, repeat=args.repeat)
        return 0

    if not args.list_models and args.max_tokens is None:
        msg = "--max-tokens is required for live runs (or use --dry-run / --list-models)"
        raise SystemExit(msg)

    client = create_gigachat_client(settings)
    uninstall_recording = None
    uninstall_rate_limits = None
    rate_limits = RateLimitCapture()
    out = OutWriter(Path(args.out) if args.out is not None else None)
    spend = SpendTracker()
    params = BenchmarkParams(
        operations=operations,
        model="",
        repeat=args.repeat,
        deadline=args.deadline,
        show_outputs=args.show_outputs,
        max_tokens=args.max_tokens,
    )
    try:
        uninstall_rate_limits = install_rate_limit_capture(client, rate_limits)
        if args.list_models or args.models is None:
            await _print_available_models(client, list_only=args.list_models)
        if args.list_models:
            return 0

        model_list = list(dict.fromkeys(args.models)) if args.models else default_models(settings)
        if args.record_fixtures:
            uninstall_recording = install_recording(
                client,
                RecordingConfig(out_dir=Path(args.record_fixtures), name_prefix="bench"),
            )

        print("Calls are sequential. One unmeasured warm-up call runs per model.")
        print(f"Price table date: {PRICE_TABLE_DATE} (₽ / 1k tokens)")
        print(f"Operations: {', '.join(operations)}")
        print(f"Max billable tokens: {args.max_tokens}")

        result = await _run_live_models(
            _ModelRun(
                client=client,
                settings=settings,
                cases=cases,
                model="",
                params=params,
                out=out,
                spend=spend,
                rate_limits=rate_limits,
            ),
            model_list,
        )
        return _finish_report(out, result)
    finally:
        if uninstall_recording is not None:
            await uninstall_recording()
        if uninstall_rate_limits is not None:
            await uninstall_rate_limits()
        await close_gigachat_client(client)


def main() -> None:
    """CLI entrypoint for ``svoi-pravila-bench-llm``."""
    parser = argparse.ArgumentParser(
        description="GigaChat generation benchmark",
        epilog=_ESTIMATE_HELP,
    )
    parser.add_argument(
        "--data",
        default="/app/benchmarks/data/bench_v2.jsonl",
        help="Path to JSONL synthetic dataset",
    )
    parser.add_argument(
        "--models",
        nargs="*",
        default=None,
        help="Models to benchmark (default: unique SP_GIGACHAT_MODEL_* from settings)",
    )
    parser.add_argument(
        "--ops",
        nargs="*",
        default=None,
        choices=list(RUN_OPERATIONS),
        help="Operations to run (default: soften help_say decode_stream)",
    )
    parser.add_argument("--repeat", type=int, default=3, help="Repeats per case (default: 3)")
    parser.add_argument("--deadline", type=float, default=30.0, help="Per-call deadline seconds")
    parser.add_argument(
        "--out",
        default=None,
        help="Write the markdown report to this path (C0 tables only)",
    )
    parser.add_argument(
        "--show-outputs",
        action="store_true",
        help="Print generated texts to stderr (off by default)",
    )
    parser.add_argument(
        "--list-models",
        action="store_true",
        help="List available models via the SDK and exit",
    )
    parser.add_argument(
        "--record-fixtures",
        default=None,
        help=(
            "Directory to write sanitized HTTP fixtures during the run. "
            "Buffers streamed responses, so latency and TTFC from a recording "
            "run are not valid measurements."
        ),
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
        help="Hard billable-token budget (required unless --dry-run or --list-models)",
    )
    parser.add_argument(
        "--smoke",
        action="store_true",
        help="Run only cases marked smoke:true in the dataset",
    )
    parser.add_argument(
        "--cases",
        nargs="+",
        default=None,
        help="Restrict to these case ids",
    )
    args = parser.parse_args()
    raise SystemExit(asyncio.run(async_main(args)))


if __name__ == "__main__":
    main()
