"""CLI entry for the rewrite eval (on demand, never from pytest)."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from svoi_pravila.adapters.llm.gigachat.adapter import GigaChatTextGenerator
from svoi_pravila.adapters.llm.gigachat.client import close_gigachat_client, create_gigachat_client
from svoi_pravila.adapters.llm.gigachat.estimate import max_billable_for_request
from svoi_pravila.adapters.llm.gigachat.judge import GigaChatRewriteJudge, max_judge_billable
from svoi_pravila.application.ports.generation import RewriteRequest
from svoi_pravila.benchmarks.llm import _print_available_models
from svoi_pravila.benchmarks.runner import SpendTracker
from svoi_pravila.config import LlmToolSettings
from svoi_pravila.evals.judge_calibration import judge_is_calibrated
from svoi_pravila.evals.rewrite_cases import RewriteCase, load_rewrite_cases, merge_cases
from svoi_pravila.evals.rewrite_gates import (
    Baseline,
    compute_metrics,
    evaluate_gates,
    gates_failed,
    read_baseline,
    write_baseline,
)
from svoi_pravila.evals.rewrite_report import render_report
from svoi_pravila.evals.rewrite_run import (
    ModelEval,
    RewriteEvalParams,
    run_rewrite_eval,
    worst_case_judge_request,
)

_DEFAULT_DATA = "/app/evals/data/rewrite_v2.jsonl"
_DEFAULT_BASELINES = "/app/evals/baselines"
_ESTIMATE_HELP = (
    "Worst case per call: ceil(rendered system+user characters / 3) + the output cap, "
    "times the maximum attempts."
)


def _load_cases(args: argparse.Namespace) -> list[RewriteCase]:
    cases = load_rewrite_cases(Path(args.data))
    if args.extra_cases is None:
        return cases
    return merge_cases(cases, load_rewrite_cases(Path(args.extra_cases)))


def _worst_case(case: RewriteCase, *, judged: bool) -> int:
    rewrite = max_billable_for_request(
        RewriteRequest(
            draft=case.draft,
            goal=case.goal,
            rules=case.rules,
            relationship=case.relationship,
            deadline_seconds=1.0,
        )
    )
    return rewrite + (max_judge_billable(worst_case_judge_request(case, 1.0)) if judged else 0)


def _models(args: argparse.Namespace, settings: LlmToolSettings) -> list[str]:
    requested = args.model or [settings.gigachat_model_rewrite]
    return list(dict.fromkeys(requested))


def _print_dry_run(
    cases: list[RewriteCase], *, repeat: int, models: list[str], judge_model: str | None
) -> None:
    worst = [_worst_case(case, judged=judge_model is not None) for case in cases]
    print("Dry-run (no network calls).")
    print(_ESTIMATE_HELP)
    print(f"Models: {', '.join(models)}")
    print(f"Judge model: {judge_model or 'none'}")
    print(f"Cases: {len(cases)}")
    print(f"Repeat: {repeat}")
    print(f"Total planned calls: {len(cases) * repeat * len(models)}")
    print(f"Total worst-case billable tokens: {sum(worst) * repeat * len(models)}")
    print(f"Largest single-call worst-case estimate: {max(worst)}")


def _write_report(out: Path, report: str) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report, encoding="utf-8")


def _read_baseline(args: argparse.Namespace) -> Baseline | None:
    return None if args.baseline is None else read_baseline(Path(args.baseline))


async def async_main(args: argparse.Namespace) -> int:
    """Run the rewrite eval and write the local Markdown report to ``--out``."""
    settings = LlmToolSettings()
    if args.dry_run:
        _print_dry_run(
            _load_cases(args),
            repeat=args.repeat,
            models=_models(args, settings),
            judge_model=args.judge_model,
        )
        return 0
    if not args.list_models and (args.max_tokens is None or args.out is None):
        msg = "--max-tokens and --out are required for live runs (or use --dry-run / --list-models)"
        raise SystemExit(msg)
    baseline = _read_baseline(args)
    client = create_gigachat_client(settings)
    try:
        await _print_available_models(client, list_only=True)
        if args.list_models:
            return 0
        cases = _load_cases(args)
        models = _models(args, settings)
        if args.write_baseline is not None and len(models) != 1:
            msg = "--write-baseline needs exactly one --model"
            raise SystemExit(msg)
        judge_calibrated = args.judge_model is not None and judge_is_calibrated(
            Path(args.baselines_dir), args.judge_model
        )
        if args.judge_model is not None and not judge_calibrated:
            print("Judge is not calibrated for this model and rubric: gates J1-J6 are n/a.")
        params = RewriteEvalParams(
            repeat=args.repeat,
            deadline_seconds=args.deadline,
            max_tokens=args.max_tokens,
            judge=(
                None if args.judge_model is None else GigaChatRewriteJudge(client, args.judge_model)
            ),
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
        report = render_report(
            evals,
            not_run=tuple(models[len(evals) :]),
            baseline=baseline,
            judge_calibrated=judge_calibrated,
        )
        await asyncio.to_thread(_write_report, Path(args.out), report)
        print(f"Report written. Spent billable tokens: {spend.spent_billable}")
        if args.write_baseline is not None:
            write_baseline(Path(args.write_baseline), compute_metrics(evals[0].result.records))
            print("Baseline written.")
        failed = any(
            gates_failed(
                evaluate_gates(
                    compute_metrics(item.result.records),
                    baseline,
                    judge_calibrated=judge_calibrated,
                )
            )
            for item in evals
        )
        return 1 if evals[-1].result.stop_reason is not None or failed else 0
    finally:
        await close_gigachat_client(client)


def main() -> None:
    """CLI entrypoint for ``svoi-pravila-eval-rewrite``."""
    parser = argparse.ArgumentParser(description="GigaChat rewrite eval", epilog=_ESTIMATE_HELP)
    parser.add_argument("--data", default=_DEFAULT_DATA, help="Path to the JSONL rewrite cases")
    parser.add_argument(
        "--extra-cases",
        default=None,
        help="Extra JSONL cases kept outside the repository (never committed)",
    )
    parser.add_argument(
        "--baseline",
        default=None,
        help="Baseline JSON of an earlier pass; gate G8 compares check_ok with it",
    )
    parser.add_argument(
        "--write-baseline",
        default=None,
        help="Write the pass numbers (rates only, no text) as a baseline JSON; one --model only",
    )
    parser.add_argument(
        "--model",
        action="append",
        default=None,
        help="Rewrite model to evaluate (repeatable); default: the model from settings",
    )
    parser.add_argument(
        "--judge-model",
        default=None,
        help="Judge model: one judge call per usable call, gates J1-J6 (n/a unless calibrated)",
    )
    parser.add_argument(
        "--baselines-dir",
        default=_DEFAULT_BASELINES,
        help="Directory of the committed judge calibration files",
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
