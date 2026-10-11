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
    AuthFailedError,
    CallRecord,
    FailFastUnavailableError,
    RateLimitedError,
    SpendTracker,
    TokenBudgetExceededError,
    _ensure_budget,
)
from svoi_pravila.evals.cases import (
    EvalCase,
    RunOperation,
    format_expected,
    order_cases_for_run,
    with_deadline,
)
from svoi_pravila.evals.estimate import estimate_eval_case_tokens
from svoi_pravila.evals.metrics import EvalRecord, format_metrics, leak_count
from svoi_pravila.evals.rule_checks import (
    variant_leaks_stems,
    variants_comply_with_closing_ask,
)


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
        had_rules=case.has_rules(),
        effect_pair=case.effect_pair,
    )


def _raise_if_fail_fast(exc: GenerationUnavailable) -> None:
    if exc.kind not in {UnavailableKind.RATE_LIMITED, UnavailableKind.AUTH}:
        return
    record = CallRecord(
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
    if exc.kind is UnavailableKind.RATE_LIMITED:
        raise RateLimitedError(record)
    raise AuthFailedError(record)


def _error_record(
    case: EvalCase,
    exc: GenerationRefusedByProvider | InvalidGenerationOutput | GenerationUnavailable,
) -> EvalRecord:
    if isinstance(exc, GenerationUnavailable):
        _raise_if_fail_fast(exc)
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
        had_rules=case.has_rules(),
        effect_pair=case.effect_pair,
    )


def _emit_output(payload: dict[str, object], *, show_outputs: bool) -> None:
    if show_outputs:
        print(json.dumps(payload, ensure_ascii=False), file=sys.stderr)


def _rule_fields(
    case: EvalCase, *, variants: tuple[str, ...], verdict: str
) -> tuple[bool | None, bool | None]:
    """Return (rule_leak, rule_compliant) C0 flags for deterministic categories."""
    rule_leak: bool | None = None
    rule_compliant: bool | None = None
    if case.category == "rule_leak" and verdict == "ok":
        rule_leak = variant_leaks_stems(
            draft=case.screen_text(),
            variants=variants,
            stop_stems=case.stop_stems,
        )
    if case.category == "rule_effect" and verdict == "ok":
        rule_compliant = variants_comply_with_closing_ask(variants)
    return rule_leak, rule_compliant


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
    variants: tuple[str, ...] = ()
    try:
        if timed.operation == "soften" and timed.soften is not None:
            generated = await gen.soften(timed.soften)
            variants = tuple(item.text for item in generated.variants)
            _emit_output(
                {"id": timed.id, "variants": list(variants)},
                show_outputs=show_outputs,
            )
            usage = generated.meta.usage
            verdict = generated.safety.value
        elif timed.operation == "help_say" and timed.help_say is not None:
            generated_h = await gen.help_say(timed.help_say)
            variants = tuple(item.text for item in generated_h.variants)
            _emit_output(
                {"id": timed.id, "variants": list(variants)},
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
            variants = tuple(item.text for item in completed.result.variants)
            _emit_output(
                {
                    "id": timed.id,
                    "variants": list(variants),
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
    rule_leak, rule_compliant = _rule_fields(timed, variants=variants, verdict=verdict)
    reasons: list[str] = []
    if rule_leak is True:
        reasons.append("rule_leak")
    return EvalRecord(
        case_id=timed.id,
        operation=timed.operation,
        category=timed.category,
        expected=timed.expected,
        outcome="ok",
        verdict=verdict,
        screen_hit=False,
        reasons=tuple(reasons),
        billable_tokens=usage.billable,
        valid=True,
        rule_leak=rule_leak,
        rule_compliant=rule_compliant,
        had_rules=timed.has_rules(),
        effect_pair=timed.effect_pair,
    )


def write_eval_out(out: OutWriter, records: list[EvalRecord]) -> None:
    """Write C0 per-case rows and metrics to ``--out``."""
    header = "| id | op | category | expected | outcome | verdict | screen | reasons | tokens |"
    out.write_header_once(header)
    out.write_header_once("|---|---|---|---|---|---|---|---|---|")
    for row in records:
        reasons = ",".join(row.reasons) if row.reasons else ""
        verdict = row.verdict or ""
        expected_cell = format_expected(row.expected)
        line = (
            f"| {row.case_id} | {row.operation} | {row.category} | {expected_cell} | "
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
        ordered = order_cases_for_run(cases, (operation,))
        live = next(
            (case for case in ordered if not runtime.screen.hit(case.screen_text())),
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
) -> tuple[
    list[EvalRecord], bool, FailFastUnavailableError | None, TokenBudgetExceededError | None
]:
    """Run selected operations; return records and incomplete flag."""
    records: list[EvalRecord] = []
    fail_fast: FailFastUnavailableError | None = None
    budget_error: TokenBudgetExceededError | None = None
    selected = order_cases_for_run(cases, params.operations)
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
    except FailFastUnavailableError as exc:
        runtime.spend.add(exc.record.billable_tokens)
        fail_fast = exc
    except TokenBudgetExceededError as exc:
        budget_error = exc
    write_eval_out(runtime.out, records)
    incomplete = fail_fast is not None or budget_error is not None
    return records, incomplete, fail_fast, budget_error


def eval_exit_code(
    records: list[EvalRecord],
    *,
    incomplete: bool,
) -> int:
    """Non-zero on leaks or an incomplete run."""
    if leak_count(records) > 0 or incomplete:
        return 1
    return 0
