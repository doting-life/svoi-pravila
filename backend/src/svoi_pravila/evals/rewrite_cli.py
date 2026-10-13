"""CLI entry for the rewrite eval (on demand, never from pytest)."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from svoi_pravila.adapters.llm.gigachat.adapter import GigaChatTextGenerator
from svoi_pravila.adapters.llm.gigachat.client import close_gigachat_client, create_gigachat_client
from svoi_pravila.adapters.llm.gigachat.estimate import max_billable_for_request
from svoi_pravila.application.ports.generation import RewriteRequest
from svoi_pravila.benchmarks.llm import _print_available_models
from svoi_pravila.benchmarks.runner import SpendTracker
from svoi_pravila.config import LlmToolSettings
from svoi_pravila.evals.rewrite_cases import RewriteCase, load_rewrite_cases, merge_cases
from svoi_pravila.evals.rewrite_report import render_report
from svoi_pravila.evals.rewrite_run import (
    ModelEval,
    RewriteEvalParams,
    RunOutcome,
    run_rewrite_eval,
)

_DEFAULT_DATA = "/app/evals/data/interview_v1.jsonl"
_ESTIMATE_HELP = (
    "Worst case per call: ceil(rendered system+user characters / 3) + the output cap, "
    "times the maximum attempts."
)


def _load_cases(args: argparse.Namespace) -> list[RewriteCase]:
    cases = load_rewrite_cases(Path(args.data))
    if args.extra_cases is None:
        return cases
    return merge_cases(cases, load_rewrite_cases(Path(args.extra_cases)))


def _worst_case(case: RewriteCase) -> int:
    return max_billable_for_request(
        RewriteRequest(
            draft=case.draft,
            goal=case.goal,
            rules=(),
            relationship=case.relationship,
            deadline_seconds=1.0,
        )
    )


def _models(args: argparse.Namespace, settings: LlmToolSettings) -> list[str]:
    requested = args.model or [settings.gigachat_model_rewrite]
    return list(dict.fromkeys(requested))


def _print_dry_run(cases: list[RewriteCase], *, repeat: int, models: list[str]) -> None:
    worst = [_worst_case(case) for case in cases]
    print("Dry-run (no network calls).")
    print(_ESTIMATE_HELP)
    print(f"Models: {', '.join(models)}")
    print(f"Cases: {len(cases)}")
    print(f"Repeat: {repeat}")
    print(f"Total planned calls: {len(cases) * repeat * len(models)}")
    print(f"Total worst-case billable tokens: {sum(worst) * repeat * len(models)}")
    print(f"Largest single-call worst-case estimate: {max(worst)}")


def _write_report(out: Path, report: str) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report, encoding="utf-8")


async def async_main(args: argparse.Namespace) -> int:
    """Run the rewrite eval and write the local Markdown report to ``--out``."""
    settings = LlmToolSettings()
    if args.dry_run:
        _print_dry_run(_load_cases(args), repeat=args.repeat, models=_models(args, settings))
        return 0
    if not args.list_models and (args.max_tokens is None or args.out is None):
        msg = "--max-tokens and --out are required for live runs (or use --dry-run / --list-models)"
        raise SystemExit(msg)
    client = create_gigachat_client(settings)
    try:
        await _print_available_models(client, list_only=True)
        if args.list_models:
            return 0
        cases = _load_cases(args)
        models = _models(args, settings)
        params = RewriteEvalParams(
            repeat=args.repeat, deadline_seconds=args.deadline, max_tokens=args.max_tokens
        )
        spend = SpendTracker()
        evals: list[ModelEval] = []
        for model in models:
            before = spend.spent_billable
            result = await run_rewrite_eval(
                GigaChatTextGenerator(
                    client, settings.model_copy(update={"gigachat_model_rewrite": model})
                ),
                cases,
                params,
                spend,
            )
            evals.append(ModelEval(model, result, spend.spent_billable - before))
            if result.stop_reason is not None:
                break
        report = render_report(evals, not_run=tuple(models[len(evals) :]))
        await asyncio.to_thread(_write_report, Path(args.out), report)
        print(f"Report written. Spent billable tokens: {spend.spent_billable}")
        failed = any(
            r.outcome is not RunOutcome.OK
            or any(v.violations or v.check_ok is False for v in r.variants)
            for item in evals
            for r in item.result.records
        )
        return 1 if evals[-1].result.stop_reason is not None or failed else 0
    finally:
        await close_gigachat_client(client)


def main() -> None:
    """CLI entrypoint for ``svoi-pravila-eval-rewrite``."""
    parser = argparse.ArgumentParser(description="GigaChat rewrite eval", epilog=_ESTIMATE_HELP)
    parser.add_argument("--data", default=_DEFAULT_DATA, help="Path to the JSONL interview cases")
    parser.add_argument(
        "--extra-cases",
        default=None,
        help="Extra JSONL cases kept outside the repository (never committed)",
    )
    parser.add_argument(
        "--model",
        action="append",
        default=None,
        help="Rewrite model to evaluate (repeatable); default: the model from settings",
    )
    parser.add_argument("--repeat", type=int, default=3, help="Calls per case (default: 3)")
    parser.add_argument("--deadline", type=float, default=30.0, help="Per-call deadline seconds")
    parser.add_argument("--out", default=None, help="Markdown report path, outside the repository")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned calls and worst-case tokens; make no network calls",
    )
    parser.add_argument(
        "--list-models",
        action="store_true",
        help="Authenticate and list models; spends no generation tokens",
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=None,
        help="Hard billable-token budget (required unless --dry-run / --list-models)",
    )
    args = parser.parse_args()
    raise SystemExit(asyncio.run(async_main(args)))


if __name__ == "__main__":
    main()
