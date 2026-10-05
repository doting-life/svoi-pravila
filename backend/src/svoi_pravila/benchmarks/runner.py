"""Execute benchmark operations and collect latency/quality metrics."""

from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass, field
from typing import Literal

from svoi_pravila.application.errors import (
    GenerationRefusedByProvider,
    GenerationUnavailable,
    InvalidGenerationOutput,
    InvalidOutputReason,
    UnavailableKind,
)
from svoi_pravila.application.ports.generation import (
    AnalysisChunk,
    DecodeCompleted,
    DecodeRequest,
    DecodeResult,
    HelpSayRequest,
    SoftenRequest,
    SuggestRuleRequest,
    SuggestRuleResult,
    TextGenerator,
    TokenUsage,
)
from svoi_pravila.benchmarks.cases import (
    BenchCase,
    RunOperation,
    cases_for_operation,
    with_deadline,
)
from svoi_pravila.benchmarks.estimate import estimate_case_tokens
from svoi_pravila.benchmarks.out_writer import OutWriter
from svoi_pravila.benchmarks.recording import RateLimitCapture
from svoi_pravila.benchmarks.report import (
    aggregate_row,
    decode_stream_report_header,
    format_reasons_line,
    standard_report_header,
)

OutcomeName = Literal["ok", "invalid_output", "refused", "unavailable"]


@dataclass
class CallRecord:
    """One measured generation attempt (after warm-up)."""

    outcome: OutcomeName
    latency_ms: float
    expected_safety: str
    attempts: int
    reasons: tuple[str, ...]
    unavailable_kind: str | None
    input_tokens: int
    output_tokens: int
    billable_tokens: int = 0
    ttfc_ms: float | None = None
    phase_a_ms: float | None = None
    phase_b_ms: float | None = None
    actual_safety: str | None = None
    output_line: str | None = None
    actual_verdict: str | None = None
    actual_category: str | None = None
    text_length: int | None = None
    overlap_violation: bool = False
    schema_valid_first_attempt: bool | None = None


@dataclass
class RunStats:
    """Aggregated records for one model and operation."""

    records: list[CallRecord] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class BenchmarkParams:
    """Arguments for one model benchmark pass."""

    operations: tuple[RunOperation, ...]
    model: str
    repeat: int
    deadline: float
    show_outputs: bool
    max_tokens: int | None = None


@dataclass(frozen=True, slots=True)
class CaseRunOptions:
    """Per-call options for ``run_case``."""

    operation: RunOperation
    deadline: float
    show_outputs: bool
    rate_limits: RateLimitCapture | None = None


@dataclass
class BenchmarkRuntime:
    """Shared writers/trackers for one benchmark pass."""

    out: OutWriter
    spend: SpendTracker
    rate_limits: RateLimitCapture | None = None


@dataclass
class SpendTracker:
    """Cumulative billable tokens for warm-up and measured calls."""

    spent_billable: int = 0
    warmup_billable: int = 0

    def add(self, billable: int, *, warmup: bool = False) -> None:
        self.spent_billable += billable
        if warmup:
            self.warmup_billable += billable


class FailFastUnavailableError(Exception):
    """Stop the run after the first auth or rate_limited unavailable outcome."""

    def __init__(
        self,
        record: CallRecord,
        *,
        message: str,
        http_status: int | None = None,
        rate_limit_headers: tuple[tuple[str, str], ...] = (),
    ) -> None:
        super().__init__(message)
        self.record = record
        self.http_status = http_status
        self.rate_limit_headers = rate_limit_headers


class RateLimitedError(FailFastUnavailableError):
    """Raised when the first rate_limited unavailable outcome is observed."""

    def __init__(
        self,
        record: CallRecord,
        *,
        http_status: int | None = None,
        rate_limit_headers: tuple[tuple[str, str], ...] = (),
    ) -> None:
        super().__init__(
            record,
            message="rate_limited",
            http_status=http_status,
            rate_limit_headers=rate_limit_headers,
        )


class AuthFailedError(FailFastUnavailableError):
    """Raised when the first auth unavailable outcome is observed."""

    def __init__(
        self,
        record: CallRecord,
        *,
        http_status: int | None = None,
        rate_limit_headers: tuple[tuple[str, str], ...] = (),
    ) -> None:
        super().__init__(
            record,
            message="auth",
            http_status=http_status,
            rate_limit_headers=rate_limit_headers,
        )


class TokenBudgetExceededError(Exception):
    """Raised when the next call would exceed ``--max-tokens``."""

    def __init__(self, *, spent: int, limit: int) -> None:
        super().__init__("token budget reached")
        self.spent = spent
        self.limit = limit


def _ensure_budget(spend: SpendTracker, estimate: int, *, max_tokens: int | None) -> None:
    if max_tokens is not None and spend.spent_billable + estimate > max_tokens:
        raise TokenBudgetExceededError(spent=spend.spent_billable, limit=max_tokens)


@dataclass
class _AttemptResult:
    outcome: OutcomeName
    attempts: int
    reasons: tuple[str, ...]
    unavailable_kind: str | None
    input_tokens: int
    output_tokens: int
    billable_tokens: int
    actual_safety: str | None
    output_line: str | None
    ttfc_ms: float | None = None
    phase_a_ms: float | None = None
    phase_b_ms: float | None = None
    actual_verdict: str | None = None
    actual_category: str | None = None
    text_length: int | None = None
    overlap_violation: bool = False
    schema_valid_first_attempt: bool | None = None


def _empty_stream_result() -> InvalidGenerationOutput:
    return InvalidGenerationOutput(
        (InvalidOutputReason.EMPTY_MESSAGE,),
        usage=TokenUsage(),
        attempts=1,
        model="bench",
        prompt_version="bench",
    )


def _usage_fields(usage: TokenUsage) -> tuple[int, int, int]:
    return usage.input, usage.output, usage.billable


async def _run_soften(
    gen: TextGenerator, request: SoftenRequest, *, case_id: str, show_outputs: bool
) -> _AttemptResult:
    soften = await gen.soften(request)
    output_line = None
    if show_outputs:
        output_line = json.dumps(
            {"id": case_id, "variants": [v.text for v in soften.variants]},
            ensure_ascii=False,
        )
    inp, out, billable = _usage_fields(soften.meta.usage)
    return _AttemptResult(
        outcome="ok",
        attempts=soften.meta.attempts,
        reasons=(),
        unavailable_kind=None,
        input_tokens=inp,
        output_tokens=out,
        billable_tokens=billable,
        actual_safety=soften.safety.value,
        output_line=output_line,
    )


async def _run_help_say(
    gen: TextGenerator, request: HelpSayRequest, *, case_id: str, show_outputs: bool
) -> _AttemptResult:
    help_say = await gen.help_say(request)
    output_line = None
    if show_outputs:
        output_line = json.dumps(
            {"id": case_id, "variants": [v.text for v in help_say.variants]},
            ensure_ascii=False,
        )
    inp, out, billable = _usage_fields(help_say.meta.usage)
    return _AttemptResult(
        outcome="ok",
        attempts=help_say.meta.attempts,
        reasons=(),
        unavailable_kind=None,
        input_tokens=inp,
        output_tokens=out,
        billable_tokens=billable,
        actual_safety=help_say.safety.value,
        output_line=output_line,
    )


def _has_verbatim_overlap(incoming: str, text: str | None, *, window: int = 30) -> bool:
    if text is None or len(incoming) < window:
        return False
    haystack = text.casefold()
    source = incoming.casefold()
    for index in range(0, len(source) - window + 1):
        if source[index : index + window] in haystack:
            return True
    return False


async def _run_suggest_rule(
    gen: TextGenerator,
    request: SuggestRuleRequest,
    *,
    case_id: str,
    show_outputs: bool,
) -> _AttemptResult:
    result: SuggestRuleResult = await gen.suggest_rule(request)
    output_line = None
    if show_outputs:
        output_line = json.dumps(
            {
                "id": case_id,
                "verdict": result.verdict.value,
                "category": None if result.category is None else result.category.value,
                "text": result.text,
            },
            ensure_ascii=False,
        )
    inp, out, billable = _usage_fields(result.meta.usage)
    text = result.text
    return _AttemptResult(
        outcome="ok",
        attempts=result.meta.attempts,
        reasons=(),
        unavailable_kind=None,
        input_tokens=inp,
        output_tokens=out,
        billable_tokens=billable,
        actual_safety="ok",
        output_line=output_line,
        actual_verdict=result.verdict.value,
        actual_category=None if result.category is None else result.category.value,
        text_length=None if text is None else len(text),
        overlap_violation=_has_verbatim_overlap(request.incoming, text),
        schema_valid_first_attempt=result.meta.attempts == 1,
    )


def _decode_output_line(case_id: str, completed: DecodeResult, *, show_outputs: bool) -> str | None:
    if not show_outputs:
        return None
    return json.dumps(
        {
            "id": case_id,
            "hypotheses": list(completed.hypotheses),
            "variants": [v.text for v in completed.variants],
        },
        ensure_ascii=False,
    )


async def _run_decode_stream(
    gen: TextGenerator,
    request: DecodeRequest,
    *,
    case_id: str,
    show_outputs: bool,
    started: float,
) -> _AttemptResult:
    first_chunk_at: float | None = None
    last_chunk_at: float | None = None
    completed: DecodeResult | None = None
    async for event in gen.decode_stream(request):
        if isinstance(event, AnalysisChunk):
            now = time.perf_counter()
            if first_chunk_at is None:
                first_chunk_at = now
            last_chunk_at = now
        if isinstance(event, DecodeCompleted):
            completed = event.result
    if completed is None:
        raise _empty_stream_result()
    finished = time.perf_counter()
    ttfc_ms = phase_a_ms = phase_b_ms = None
    if first_chunk_at is not None:
        ttfc_ms = (first_chunk_at - started) * 1000
        phase_a_ms = (last_chunk_at - started) * 1000 if last_chunk_at else ttfc_ms
        phase_b_ms = (finished - last_chunk_at) * 1000 if last_chunk_at else 0.0
    inp, out, billable = _usage_fields(completed.meta.usage)
    return _AttemptResult(
        outcome="ok",
        attempts=completed.meta.attempts,
        reasons=(),
        unavailable_kind=None,
        input_tokens=inp,
        output_tokens=out,
        billable_tokens=billable,
        actual_safety=completed.safety.value,
        output_line=_decode_output_line(case_id, completed, show_outputs=show_outputs),
        ttfc_ms=ttfc_ms,
        phase_a_ms=phase_a_ms,
        phase_b_ms=phase_b_ms,
    )


def _apply_error(attempt: _AttemptResult, exc: BaseException) -> None:
    if isinstance(exc, GenerationRefusedByProvider):
        attempt.outcome = "refused"
        attempt.attempts = exc.attempts
        inp, out, billable = _usage_fields(exc.usage)
        attempt.input_tokens = inp
        attempt.output_tokens = out
        attempt.billable_tokens = billable
        return
    if isinstance(exc, InvalidGenerationOutput):
        attempt.outcome = "invalid_output"
        attempt.reasons = tuple(r.value for r in exc.reasons)
        attempt.attempts = exc.attempts
        inp, out, billable = _usage_fields(exc.usage)
        attempt.input_tokens = inp
        attempt.output_tokens = out
        attempt.billable_tokens = billable
        return
    if isinstance(exc, GenerationUnavailable):
        attempt.outcome = "unavailable"
        attempt.unavailable_kind = exc.kind.value
        attempt.attempts = exc.attempts
        inp, out, billable = _usage_fields(exc.usage)
        attempt.input_tokens = inp
        attempt.output_tokens = out
        attempt.billable_tokens = billable
        return
    raise exc


async def run_case(
    gen: TextGenerator,
    case: BenchCase,
    options: CaseRunOptions,
) -> CallRecord:
    """Execute one validated case. Only application generation errors are caught."""
    timed = with_deadline(case, options.deadline)
    started = time.perf_counter()
    if options.rate_limits is not None:
        options.rate_limits.clear()
    attempt = _AttemptResult(
        outcome="ok",
        attempts=0,
        reasons=(),
        unavailable_kind=None,
        input_tokens=0,
        output_tokens=0,
        billable_tokens=0,
        actual_safety=None,
        output_line=None,
    )
    try:
        if options.operation == "soften" and timed.soften is not None:
            attempt = await _run_soften(
                gen, timed.soften, case_id=timed.id, show_outputs=options.show_outputs
            )
        elif options.operation == "help_say" and timed.help_say is not None:
            attempt = await _run_help_say(
                gen, timed.help_say, case_id=timed.id, show_outputs=options.show_outputs
            )
        elif options.operation == "decode_stream" and timed.decode is not None:
            attempt = await _run_decode_stream(
                gen,
                timed.decode,
                case_id=timed.id,
                show_outputs=options.show_outputs,
                started=started,
            )
        elif options.operation == "suggest_rule" and timed.suggest_rule is not None:
            attempt = await _run_suggest_rule(
                gen,
                timed.suggest_rule,
                case_id=timed.id,
                show_outputs=options.show_outputs,
            )
        else:
            msg = "validated case missing request for operation"
            raise ValueError(msg)
    except (GenerationRefusedByProvider, InvalidGenerationOutput, GenerationUnavailable) as exc:
        _apply_error(attempt, exc)
        if isinstance(exc, InvalidGenerationOutput):
            attempt.schema_valid_first_attempt = False
    latency_ms = (time.perf_counter() - started) * 1000
    ok = attempt.outcome == "ok"
    record = CallRecord(
        outcome=attempt.outcome,
        latency_ms=latency_ms,
        expected_safety=case.expected_safety,
        ttfc_ms=attempt.ttfc_ms if ok else None,
        phase_a_ms=attempt.phase_a_ms if ok else None,
        phase_b_ms=attempt.phase_b_ms if ok else None,
        attempts=attempt.attempts,
        reasons=attempt.reasons,
        unavailable_kind=attempt.unavailable_kind,
        input_tokens=attempt.input_tokens,
        output_tokens=attempt.output_tokens,
        billable_tokens=attempt.billable_tokens,
        actual_safety=attempt.actual_safety if ok else None,
        output_line=attempt.output_line,
        actual_verdict=attempt.actual_verdict if ok else None,
        actual_category=attempt.actual_category if ok else None,
        text_length=attempt.text_length if ok else None,
        overlap_violation=attempt.overlap_violation if ok else False,
        schema_valid_first_attempt=attempt.schema_valid_first_attempt,
    )
    _raise_if_fail_fast(record, rate_limits=options.rate_limits)
    return record


def _raise_if_fail_fast(
    record: CallRecord,
    *,
    rate_limits: RateLimitCapture | None,
) -> None:
    kind = record.unavailable_kind
    if kind == UnavailableKind.RATE_LIMITED.value:
        capture = rate_limits
        raise RateLimitedError(
            record,
            http_status=capture.http_status if capture is not None else None,
            rate_limit_headers=capture.rate_limit_headers if capture is not None else (),
        )
    if kind == UnavailableKind.AUTH.value:
        raise AuthFailedError(record)


async def warmup(
    gen: TextGenerator,
    cases: list[BenchCase],
    params: BenchmarkParams,
    runtime: BenchmarkRuntime,
) -> None:
    """One unmeasured warm-up call per model, using the first selected operation."""
    for operation in params.operations:
        op_cases = cases_for_operation(cases, operation)
        if op_cases:
            case = op_cases[0]
            _ensure_budget(
                runtime.spend,
                estimate_case_tokens(case, operation),
                max_tokens=params.max_tokens,
            )
            record = await run_case(
                gen,
                case,
                CaseRunOptions(
                    operation=operation,
                    deadline=params.deadline,
                    show_outputs=False,
                    rate_limits=runtime.rate_limits,
                ),
            )
            runtime.spend.add(record.billable_tokens, warmup=True)
            return
    msg = "no cases available for selected operations"
    raise ValueError(msg)


def _emit_outputs(records: list[CallRecord], *, show_outputs: bool) -> None:
    if not show_outputs:
        return
    for rec in records:
        if rec.output_line:
            print(rec.output_line, file=sys.stderr)


@dataclass(frozen=True, slots=True)
class _OperationRun:
    gen: TextGenerator
    cases: list[BenchCase]
    operation: RunOperation
    params: BenchmarkParams
    runtime: BenchmarkRuntime


async def _run_operation(
    run: _OperationRun,
) -> tuple[list[str], bool, FailFastUnavailableError | None, TokenBudgetExceededError | None]:
    op_cases = cases_for_operation(run.cases, run.operation)
    if not op_cases:
        return [], False, None, None
    header = (
        decode_stream_report_header()
        if run.operation == "decode_stream"
        else standard_report_header()
    )
    run.runtime.out.write_header_once(header)
    stats = RunStats()
    rate_error: FailFastUnavailableError | None = None
    budget_error: TokenBudgetExceededError | None = None
    try:
        for _ in range(run.params.repeat):
            for case in op_cases:
                _ensure_budget(
                    run.runtime.spend,
                    estimate_case_tokens(case, run.operation),
                    max_tokens=run.params.max_tokens,
                )
                record = await run_case(
                    run.gen,
                    case,
                    CaseRunOptions(
                        operation=run.operation,
                        deadline=run.params.deadline,
                        show_outputs=run.params.show_outputs,
                        rate_limits=run.runtime.rate_limits,
                    ),
                )
                run.runtime.spend.add(record.billable_tokens)
                stats.records.append(record)
    except FailFastUnavailableError as exc:
        run.runtime.spend.add(exc.record.billable_tokens)
        stats.records.append(exc.record)
        rate_error = exc
    except TokenBudgetExceededError as exc:
        budget_error = exc
    row, reasons = aggregate_row(stats.records, operation=run.operation, model=run.params.model)
    run.runtime.out.write_row(row)
    run.runtime.out.write_reasons(format_reasons_line(reasons))
    if reasons:
        print(f"reasons {run.operation} {run.params.model}: {reasons}")
    _emit_outputs(stats.records, show_outputs=run.params.show_outputs)
    incomplete = rate_error is not None or budget_error is not None
    return [header, row], incomplete, rate_error, budget_error


async def run_benchmark(
    gen: TextGenerator,
    cases: list[BenchCase],
    params: BenchmarkParams,
    runtime: BenchmarkRuntime | None = None,
) -> tuple[list[str], bool, FailFastUnavailableError | None, TokenBudgetExceededError | None]:
    """Run selected operations for one model; return table rows and incomplete flag."""
    active = runtime if runtime is not None else BenchmarkRuntime(OutWriter(None), SpendTracker())
    rows: list[str] = []
    for operation in params.operations:
        op_rows, incomplete, rate_error, budget_error = await _run_operation(
            _OperationRun(
                gen=gen,
                cases=cases,
                operation=operation,
                params=params,
                runtime=active,
            )
        )
        rows.extend(op_rows)
        if incomplete:
            return rows, True, rate_error, budget_error
    return rows, False, None, None
