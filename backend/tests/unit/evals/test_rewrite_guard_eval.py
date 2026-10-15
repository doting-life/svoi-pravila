"""Eval side of the lexicon guard: G1, G2, the attempt recorder and the diagnostics report."""

from __future__ import annotations

from dataclasses import replace

import pytest
from tests.fakes.generation import FakeTextGenerator

from svoi_pravila.application.errors import (
    GenerationUnavailable,
    InvalidGenerationOutput,
    InvalidOutputReason,
    UnavailableKind,
)
from svoi_pravila.application.ports.generation import (
    GenerationMeta,
    RewriteRequest,
    RewriteResult,
    SafetyVerdict,
    TokenUsage,
    Variant,
)
from svoi_pravila.application.ports.generation_attempt_observer import InvalidAttempt
from svoi_pravila.benchmarks.runner import SpendTracker
from svoi_pravila.domain.enums import VariantKind
from svoi_pravila.evals.attempt_recorder import AttemptRecorder
from svoi_pravila.evals.rewrite_cases import RewriteCase, RewriteCategory
from svoi_pravila.evals.rewrite_gates import (
    GateResult,
    GateStatus,
    compute_metrics,
    evaluate_gates,
)
from svoi_pravila.evals.rewrite_report import render_report
from svoi_pravila.evals.rewrite_run import (
    ModelEval,
    RewriteEvalParams,
    RewriteEvalResult,
    RewriteRunRecord,
    run_rewrite_eval,
)

_GOOD = (
    Variant(text="Сиди дома и жди", kind=VariantKind.CHECK),
    Variant(text="Пожалуйста, посиди дома", kind=VariantKind.SOFTER),
    Variant(text="Жди дома", kind=VariantKind.FIRMER),
)
_CASE = RewriteCase(id="c1", category=RewriteCategory.BASE, draft="Сиди дома и жди")


def _result(
    *, repaired: tuple[str, ...] = (), residual: tuple[VariantKind, ...] = ()
) -> RewriteResult:
    return RewriteResult(
        variants=_GOOD,
        applied_rule_indexes=(),
        safety=SafetyVerdict.OK,
        meta=GenerationMeta(
            model="fake",
            prompt_version="rewrite@v1",
            latency_ms=7,
            attempts=2 if repaired else 1,
            usage=TokenUsage(input=3, output=4),
            repaired=repaired,
            residual=residual,
        ),
    )


def _failed(*reasons: InvalidOutputReason) -> InvalidGenerationOutput:
    return InvalidGenerationOutput(
        reasons,
        usage=TokenUsage(output=3),
        attempts=len(reasons),
        model="m",
        prompt_version="p",
    ).with_diagnostics(tuple(VariantKind.FIRMER for _ in reasons), tuple("stop" for _ in reasons))


async def _records(*gens: FakeTextGenerator) -> list[RewriteRunRecord]:
    records: list[RewriteRunRecord] = []
    for gen in gens:
        outcome = await run_rewrite_eval(
            gen,
            [_CASE],
            RewriteEvalParams(repeat=1, deadline_seconds=5.0, max_tokens=100_000),
            SpendTracker(),
        )
        records.extend(outcome.records)
    return records


def _gates(records: list[RewriteRunRecord]) -> dict[str, GateResult]:
    results = evaluate_gates(compute_metrics(records), None, judge_calibrated=True)
    return {result.gate: result for result in results}


def _clean() -> FakeTextGenerator:
    return FakeTextGenerator(rewrite_result=_result())


def _repaired_profanity() -> FakeTextGenerator:
    return FakeTextGenerator(rewrite_result=_result(repaired=("profanity:firmer",)))


def _still_profane() -> FakeTextGenerator:
    gen = FakeTextGenerator()
    gen.rewrite_error = _failed(InvalidOutputReason.PROFANITY, InvalidOutputReason.PROFANITY)
    return gen


@pytest.mark.unit
async def test_g1_passes_when_few_calls_had_obscene_words_and_all_were_repaired() -> None:
    records = await _records(_repaired_profanity(), *[_clean() for _ in range(9)])
    gates = _gates(records)
    assert gates["G1a"].status is GateStatus.PASSED
    assert gates["G1a"].value == "10.0 %"
    assert gates["G1b"].status is GateStatus.PASSED
    assert gates["G1b"].value == "100.0 %"


@pytest.mark.unit
async def test_g1_fails_on_a_high_first_attempt_rate_and_a_low_repair_rate() -> None:
    records = await _records(_repaired_profanity(), _still_profane(), _clean(), _clean())
    metrics = compute_metrics(records)
    assert metrics.profanity_first_attempt_runs == 2
    assert metrics.profanity_first_attempt_share == 0.5
    assert metrics.profanity_repair_share == 0.5
    gates = _gates(records)
    assert gates["G1a"].status is GateStatus.FAILED
    assert gates["G1b"].status is GateStatus.FAILED
    assert gates["G9"].status is GateStatus.FAILED


@pytest.mark.unit
async def test_g1_repair_rate_is_not_applicable_without_a_first_attempt_obscene_word() -> None:
    gates = _gates(await _records(_clean(), _clean()))
    assert gates["G1a"].status is GateStatus.PASSED
    assert gates["G1b"].status is GateStatus.NOT_APPLICABLE
    assert gates["G1b"].value == "no first-attempt obscene words"


@pytest.mark.unit
async def test_g1_ignores_formal_defects_refusals_and_unavailable_calls() -> None:
    formal_repaired = FakeTextGenerator(rewrite_result=_result(repaired=("empty_text:softer",)))
    formal_then_profane = FakeTextGenerator()
    formal_then_profane.rewrite_error = _failed(
        InvalidOutputReason.EMPTY_TEXT, InvalidOutputReason.PROFANITY
    )
    unavailable = FakeTextGenerator()
    unavailable.rewrite_error = GenerationUnavailable(
        UnavailableKind.TIMEOUT, usage=TokenUsage(), attempts=1, model="m", prompt_version="p"
    )
    refused_without_diagnostics = FakeTextGenerator()
    refused_without_diagnostics.rewrite_error = InvalidGenerationOutput(
        (InvalidOutputReason.EMPTY_MESSAGE,),
        usage=TokenUsage(),
        attempts=1,
        model="m",
        prompt_version="p",
    )
    records = await _records(
        formal_repaired,
        formal_then_profane,
        unavailable,
        refused_without_diagnostics,
        _clean(),
    )
    metrics = compute_metrics(records)
    assert metrics.profanity_first_attempt_runs == 0
    assert metrics.profanity_first_attempt_share == 0.0


@pytest.mark.unit
async def test_g2_counts_the_generalization_slots_left_and_never_fails() -> None:
    left = FakeTextGenerator(
        rewrite_result=_result(
            repaired=("generalization:softer",),
            residual=(VariantKind.SOFTER, VariantKind.FIRMER),
        )
    )
    records = await _records(left, _clean())
    gates = _gates(records)
    assert gates["G2"].value == "2 slots"
    assert gates["G2"].threshold == "info"
    assert gates["G2"].status is GateStatus.NOT_APPLICABLE
    assert records[0].residual == (VariantKind.SOFTER, VariantKind.FIRMER)


class _ObservedGenerator(FakeTextGenerator):
    """Reports one invalid attempt to the observer before every rewrite."""

    def __init__(
        self, observer: AttemptRecorder, *, rewrite_result: RewriteResult | None = None
    ) -> None:
        super().__init__(rewrite_result=rewrite_result)
        self._observer = observer

    async def rewrite(self, request: RewriteRequest) -> RewriteResult:
        self._observer.on_invalid_attempt(
            InvalidAttempt(
                operation="rewrite",
                attempt=0,
                reason=InvalidOutputReason.PROFANITY,
                slot=VariantKind.FIRMER,
                texts=(("firmer", "слово | с чертой"),),
            )
        )
        return await super().rewrite(request)


@pytest.mark.unit
async def test_the_loop_collects_the_attempts_of_each_call_under_its_case_and_run() -> None:
    recorder = AttemptRecorder()
    gen = _ObservedGenerator(recorder, rewrite_result=_result(repaired=("profanity:firmer",)))
    result = await run_rewrite_eval(
        gen,
        [_CASE],
        RewriteEvalParams(repeat=2, deadline_seconds=5.0, max_tokens=100_000, recorder=recorder),
        SpendTracker(),
    )
    assert [(row.case_id, row.run_index, row.attempt) for row in result.attempts] == [
        ("c1", 1, 1),
        ("c1", 2, 1),
    ]
    assert recorder.take() == ()


@pytest.mark.unit
async def test_the_loop_collects_attempts_of_a_call_that_ends_unavailable() -> None:
    recorder = AttemptRecorder()
    gen = _ObservedGenerator(recorder)
    gen.rewrite_error = GenerationUnavailable(
        UnavailableKind.TIMEOUT, usage=TokenUsage(), attempts=1, model="m", prompt_version="p"
    )
    result = await run_rewrite_eval(
        gen,
        [_CASE],
        RewriteEvalParams(repeat=1, deadline_seconds=5.0, max_tokens=100_000, recorder=recorder),
        SpendTracker(),
    )
    assert len(result.attempts) == 1
    assert result.records[0].outcome.value == "unavailable"


@pytest.mark.unit
async def test_the_report_shows_the_texts_of_invalid_attempts_in_the_diagnostics_section() -> None:
    recorder = AttemptRecorder()
    gen = _ObservedGenerator(recorder, rewrite_result=_result(repaired=("profanity:firmer",)))
    result = await run_rewrite_eval(
        gen,
        [_CASE],
        RewriteEvalParams(repeat=1, deadline_seconds=5.0, max_tokens=100_000, recorder=recorder),
        SpendTracker(),
    )
    report = render_report([ModelEval("m", result, 0)])
    assert "### Invalid output diagnostics" in report
    assert "| c1 | 1 | 1 | profanity | firmer | firmer: слово \\| с чертой |" in report
    assert "| G1a |" in report
    assert "| G1b |" in report
    assert "| G2 |" in report


@pytest.mark.unit
def test_an_attempt_without_a_slot_is_listed_with_slot_none() -> None:
    recorder = AttemptRecorder()
    recorder.begin_call("c9", 3)
    recorder.on_invalid_attempt(
        InvalidAttempt(
            operation="rewrite",
            attempt=1,
            reason=InvalidOutputReason.JSON_DECODE,
            slot=None,
            texts=(("raw", "not json"),),
        )
    )
    report = render_report([ModelEval("m", RewriteEvalResult(attempts=list(recorder.take())), 0)])
    assert "| c9 | 3 | 2 | json_decode | none | raw: not json |" in report


@pytest.mark.unit
def test_the_recorded_attempt_keeps_its_texts_out_of_repr() -> None:
    recorder = AttemptRecorder()
    recorder.on_invalid_attempt(
        InvalidAttempt(
            operation="rewrite",
            attempt=0,
            reason=InvalidOutputReason.PROFANITY,
            slot=VariantKind.SOFTER,
            texts=(("softer", "CANARY-text"),),
        )
    )
    (row,) = recorder.take()
    assert "CANARY" not in repr(row)
    assert replace(row, case_id="x").texts == (("softer", "CANARY-text"),)
