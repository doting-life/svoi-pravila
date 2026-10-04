"""Execute safety-eval cases with the crisis screen and generation port."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass

from svoi_pravila.application.crisis_screen import CrisisScreen
from svoi_pravila.application.errors import (
    GenerationRefusedByProvider,
    GenerationUnavailable,
    InvalidGenerationOutput,
    UnavailableKind,
)
from svoi_pravila.application.ports.generation import DecodeCompleted, TextGenerator
from svoi_pravila.benchmarks.out_writer import OutWriter
from svoi_pravila.benchmarks.recording import RateLimitCapture
from svoi_pravila.benchmarks.runner import (
    CallRecord,
    RateLimitedError,
    SpendTracker,
    TokenBudgetExceededError,
    _ensure_budget,
)
from svoi_pravila.evals.cases import EvalCase, RunOperation, with_deadline
from svoi_pravila.evals.estimate import estimate_eval_case_tokens
from svoi_pravila.evals.metrics import EvalRecord, format_metrics, leak_count


@dataclass(frozen=True, slots=True)
class EvalParams:
    """Arguments for one eval pass."""

    operations: tuple[RunOperation, ...]
    models: tuple[str, ...]
    deadline: float
    show_outputs: bool
    max_tokens: int | None = None


@dataclass
class EvalRuntime:
    """Shared writers/trackers for one eval pass."""

    out: OutWriter
    spend: SpendTracker
    screen: CrisisScreen
    rate_limits: RateLimitCapture | None = None


def _screen_record(case: EvalCase) -> EvalRecord:
    return EvalRecord(
        case_id=case.id,
        operation=case.operation,
        category=case.category,
        expected=case.expected,
        outcome="screened",
        verdict="crisis",
        screen_hit=True,
        reasons=(),
        billable_tokens=0,
        valid=True,
    )


def _raise_if_rate_limited(exc: GenerationUnavailable) -> None:
    if exc.kind is not UnavailableKind.RATE_LIMITED:
        return
    raise RateLimitedError(
        CallRecord(
            outcome="unavailable",
            latency_ms=0,
            expected_safety="ok",
            attempts=exc.attempts,
            reasons=(),
            unavailable_kind=exc.kind.value,
            input_tokens=exc.usage.input,
            output_tokens=exc.usage.output,
            billable_tokens=exc.usage.billable,
        )
    )


def _error_record(
    case: EvalCase,
    exc: GenerationRefusedByProvider | InvalidGenerationOutput | GenerationUnavailable,
) -> EvalRecord:
    if isinstance(exc, GenerationUnavailable):
        _raise_if_rate_limited(exc)
        outcome = "unavailable"
        reasons: tuple[str, ...] = ()
        billable = exc.usage.billable
    elif isinstance(exc, GenerationRefusedByProvider):
        outcome = "refused"
        reasons = ()
        billable = exc.usage.billable
    else:
        outcome = "invalid_output"
        reasons = tuple(reason.value for reason in exc.reasons)
        billable = exc.usage.billable
    return EvalRecord(
        case_id=case.id,
        operation=case.operation,
        category=case.category,
        expected=case.expected,
        outcome=outcome,
        verdict=None,
        screen_hit=False,
        reasons=reasons,
        billable_tokens=billable,
        valid=False,
    )


def _emit_output(payload: dict[str, object], *, show_outputs: bool) -> None:
    if show_outputs:
        print(json.dumps(payload, ensure_ascii=False), file=sys.stderr)


async def run_eval_case(
    gen: TextGenerator,
    case: EvalCase,
    runtime: EvalRuntime,
    *,
    deadline: float,
    show_outputs: bool,
) -> EvalRecord:
    """Screen, then optionally generate. Never writes generated text to ``--out``."""
    timed = with_deadline(case, deadline)
    if runtime.screen.hit(timed.screen_text()):
        return _screen_record(timed)
    try:
        if timed.operation == "soften" and timed.soften is not None:
            generated = await gen.soften(timed.soften)
            _emit_output(
                {"id": timed.id, "variants": [item.text for item in generated.variants]},
                show_outputs=show_outputs,
            )
            usage = generated.meta.usage
            verdict = generated.safety.value
        elif timed.operation == "help_say" and timed.help_say is not None:
            generated_h = await gen.help_say(timed.help_say)
            _emit_output(
                {"id": timed.id, "variants": [item.text for item in generated_h.variants]},
                show_outputs=show_outputs,
            )
            usage = generated_h.meta.usage
            verdict = generated_h.safety.value
        elif timed.decode is not None:
            completed: DecodeCompleted | None = None
            async for event in gen.decode_stream(timed.decode):
                if isinstance(event, DecodeCompleted):
                    completed = event
            if completed is None:
                msg = "decode_stream produced no completed event"
                raise RuntimeError(msg)
            _emit_output(
                {
                    "id": timed.id,
                    "variants": [item.text for item in completed.result.variants],
                },
                show_outputs=show_outputs,
            )
            usage = completed.result.meta.usage
            verdict = completed.result.safety.value
        else:
            msg = f"case {case.id} missing request"
            raise ValueError(msg)
    except (GenerationRefusedByProvider, InvalidGenerationOutput, GenerationUnavailable) as exc:
        return _error_record(timed, exc)
    return EvalRecord(
        case_id=timed.id,
        operation=timed.operation,
        category=timed.category,
        expected=timed.expected,
        outcome="ok",
        verdict=verdict,
        screen_hit=False,
        reasons=(),
        billable_tokens=usage.billable,
        valid=True,
    )


def write_eval_out(out: OutWriter, records: list[EvalRecord]) -> None:
    """Write C0 per-case rows and metrics to ``--out``."""
    header = "| id | op | category | expected | outcome | verdict | screen | reasons | tokens |"
    out.write_header_once(header)
    out.write_header_once("|---|---|---|---|---|---|---|---|---|")
    for row in records:
        reasons = ",".join(row.reasons) if row.reasons else ""
        verdict = row.verdict or ""
        line = (
            f"| {row.case_id} | {row.operation} | {row.category} | {row.expected} | "
            f"{row.outcome} | {verdict} | {str(row.screen_hit).lower()} | {reasons} | "
            f"{row.billable_tokens} |"
        )
        out.write_row(line)
    out.write_row("")
    for line in format_metrics(records).splitlines():
        out.write_row(line)


async def warmup_eval(
    gen: TextGenerator,
    cases: list[EvalCase],
    params: EvalParams,
    runtime: EvalRuntime,
) -> None:
    """One unmeasured warm-up per distinct model that will actually be called."""
    if len(params.models) != len(params.operations):
        msg = "models must align 1:1 with operations"
        raise ValueError(msg)
    warmed: set[str] = set()
    for model, operation in zip(params.models, params.operations, strict=True):
        if model in warmed:
            continue
        live = next(
            (
                case
                for case in cases
                if case.operation == operation and not runtime.screen.hit(case.screen_text())
            ),
            None,
        )
        if live is None:
            continue
        _ensure_budget(
            runtime.spend,
            estimate_eval_case_tokens(live, runtime.screen),
            max_tokens=params.max_tokens,
        )
        record = await run_eval_case(
            gen,
            live,
            runtime,
            deadline=params.deadline,
            show_outputs=False,
        )
        runtime.spend.add(record.billable_tokens, warmup=True)
        warmed.add(model)
    if not warmed:
        msg = "no live cases available for warm-up"
        raise ValueError(msg)


async def run_eval(
    gen: TextGenerator,
    cases: list[EvalCase],
    params: EvalParams,
    runtime: EvalRuntime,
) -> tuple[list[EvalRecord], bool, RateLimitedError | None, TokenBudgetExceededError | None]:
    """Run selected operations; return records and incomplete flag."""
    records: list[EvalRecord] = []
    rate_error: RateLimitedError | None = None
    budget_error: TokenBudgetExceededError | None = None
    selected = [case for case in cases if case.operation in params.operations]
    try:
        for case in selected:
            _ensure_budget(
                runtime.spend,
                estimate_eval_case_tokens(case, runtime.screen),
                max_tokens=params.max_tokens,
            )
            record = await run_eval_case(
                gen,
                case,
                runtime,
                deadline=params.deadline,
                show_outputs=params.show_outputs,
            )
            runtime.spend.add(record.billable_tokens)
            records.append(record)
    except RateLimitedError as exc:
        runtime.spend.add(exc.record.billable_tokens)
        rate_error = exc
    except TokenBudgetExceededError as exc:
        budget_error = exc
    write_eval_out(runtime.out, records)
    incomplete = rate_error is not None or budget_error is not None
    return records, incomplete, rate_error, budget_error


def eval_exit_code(
    records: list[EvalRecord],
    *,
    incomplete: bool,
) -> int:
    """Non-zero on leaks or an incomplete run."""
    if leak_count(records) > 0 or incomplete:
        return 1
    return 0
