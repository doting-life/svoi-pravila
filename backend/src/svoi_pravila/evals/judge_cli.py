"""CLI entry for the judge calibration (on demand, never from pytest)."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from svoi_pravila.adapters.llm.gigachat.client import close_gigachat_client, create_gigachat_client
from svoi_pravila.adapters.llm.gigachat.judge import GigaChatRewriteJudge, max_judge_billable
from svoi_pravila.benchmarks.llm import _print_available_models
from svoi_pravila.benchmarks.runner import SpendTracker
from svoi_pravila.config import LlmToolSettings
from svoi_pravila.evals.judge_calibration import (
    calibrate,
    judge_request,
    render_calibration_report,
    run_calibration,
    write_calibration,
)
from svoi_pravila.evals.judge_cases import JudgeCase, load_judge_cases

_DEFAULT_DATA = "/app/evals/data/judge_calibration_v1.jsonl"
_DEFAULT_BASELINES = "/app/evals/baselines"
_DEFAULT_OUT = "/app/evals/out/judge_calibration.md"
_ESTIMATE_HELP = (
    "Worst case per call: ceil(rendered system+user characters / 3) + the output cap, "
    "times the maximum attempts."
)


def _print_dry_run(cases: list[JudgeCase], *, model: str) -> None:
    worst = [max_judge_billable(judge_request(case, 1.0)) for case in cases]
    print("Dry-run (no network calls).")
    print(_ESTIMATE_HELP)
    print(f"Model: {model}")
    print(f"Items: {len(cases)}")
    print(f"Total planned calls: {len(cases)}")
    print(f"Total worst-case billable tokens: {sum(worst)}")
    print(f"Largest single-call worst-case estimate: {max(worst)}")


def _write_report(out: Path, report: str) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report, encoding="utf-8")


async def async_main(args: argparse.Namespace) -> int:
    """Calibrate the judge model on the items; write the report and the committed file."""
    if args.dry_run:
        _print_dry_run(load_judge_cases(Path(args.data)), model=args.model)
        return 0
    if not args.list_models and args.max_tokens is None:
        msg = "--max-tokens is required for live runs (or use --dry-run / --list-models)"
        raise SystemExit(msg)
    settings = LlmToolSettings()
    client = create_gigachat_client(settings)
    try:
        await _print_available_models(client, list_only=True)
        if args.list_models:
            return 0
        spend = SpendTracker()
        run = await run_calibration(
            GigaChatRewriteJudge(client, args.model),
            load_judge_cases(Path(args.data)),
            deadline_seconds=args.deadline,
            max_tokens=args.max_tokens,
            spend=spend,
        )
        calibration = calibrate(args.model, run.outcomes)
        report = render_calibration_report(
            calibration, run.outcomes, spent_billable=run.spent_billable
        )
        await asyncio.to_thread(_write_report, Path(args.out), report)
        print(f"Report written. Spent billable tokens: {run.spent_billable}")
        if run.stop_reason is not None:
            print(f"Run incomplete ({run.stop_reason}); no calibration file written.")
            return 1
        path = await asyncio.to_thread(write_calibration, Path(args.baselines_dir), calibration)
        print(f"Calibration written: {path.name} (passed: {calibration.passed})")
        return 0 if calibration.passed else 1
    finally:
        await close_gigachat_client(client)


def main() -> None:
    """CLI entrypoint for ``svoi-pravila-eval-judge``."""
    parser = argparse.ArgumentParser(description="Rewrite judge calibration", epilog=_ESTIMATE_HELP)
    parser.add_argument("--model", required=True, help="Judge model to calibrate")
    parser.add_argument("--data", default=_DEFAULT_DATA, help="Path to the calibration JSONL")
    parser.add_argument(
        "--baselines-dir",
        default=_DEFAULT_BASELINES,
        help="Directory of the committed judge_calibration_<model>_v1.json file",
    )
    parser.add_argument("--deadline", type=float, default=30.0, help="Per-call deadline seconds")
    parser.add_argument("--out", default=_DEFAULT_OUT, help="Markdown report path (local only)")
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
