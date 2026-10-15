"""Run the rewrite eval: N calls per case, the policy checked on the whole variant set."""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from enum import StrEnum

from svoi_pravila.application.errors import (
    GenerationRefusedByProvider,
    GenerationUnavailable,
    InvalidGenerationOutput,
    UnavailableKind,
)
from svoi_pravila.application.ports.generation import (
    RewriteRequest,
    SafetyVerdict,
    TextGenerator,
    Variant,
)
from svoi_pravila.application.ports.rewrite_judge import (
    JudgeRequest,
    RewriteJudge,
    SlotVerdicts,
    Verdict,
)
from svoi_pravila.application.rewrite_variants import fill_empty_check
from svoi_pravila.benchmarks.runner import SpendTracker
from svoi_pravila.domain.enums import RewriteGoal, VariantKind
from svoi_pravila.domain.typography import humanize_typography
from svoi_pravila.domain.variant_policy import (
    LENGTH_FACTOR,
    PolicyContext,
    PolicyRule,
    check_variant_set,
)
from svoi_pravila.evals.judge_verdicts import derive_result
from svoi_pravila.evals.rewrite_cases import RewriteCase, RewriteCategory
from svoi_pravila.evals.rule_checks import variant_complies_with_closing_ask, variant_leaks_stems

_STOP_KINDS = frozenset({UnavailableKind.RATE_LIMITED, UnavailableKind.AUTH})


class RunOutcome(StrEnum):
    """Result of one provider call."""

    OK = "ok"
    NOT_OK_SAFETY = "not_ok_safety"
    REFUSED = "refused"
    INVALID_OUTPUT = "invalid_output"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class RunVariant:
    """One variant after typography: the policy rules it broke and the case-level checks.

    ``stem_leak`` is set on ``softer`` and ``firmer`` of a case with forbidden stems;
    ``closing_ask_ok`` on every variant of a case that belongs to an effect pair.
    """

    kind: VariantKind
    text: str
    violations: tuple[PolicyRule, ...]
    check_ok: bool | None = None
    stem_leak: bool | None = None
    closing_ask_ok: bool | None = None


@dataclass(frozen=True, slots=True)
class InvalidDiagnostic:
    """C0 diagnostics of one failed attempt: ``reason[:slot]`` and the provider finish_reason."""

    reason: str
    finish_reason: str | None


@dataclass(frozen=True, slots=True)
class RunJudgement:
    """The judge's verdicts for one usable call; verdicts only, no text."""

    softer: SlotVerdicts
    firmer: SlotVerdicts
    ladder_ok: Verdict


@dataclass(frozen=True, slots=True)
class RewriteRunRecord:
    """One call for one case. Texts exist only for the local report.

    A usable call is judged when a judge is configured: ``judgement`` holds its verdicts, or
    ``judge_failed`` is set when the judge call produced none. ``billable_tokens`` covers both
    calls.
    """

    case_id: str
    category: RewriteCategory
    goal: RewriteGoal | None
    had_rules: bool
    run_index: int
    outcome: RunOutcome
    variants: tuple[RunVariant, ...]
    latency_ms: int | None
    billable_tokens: int
    diagnostics: tuple[InvalidDiagnostic, ...] = ()
    check_empty: bool = False
    judgement: RunJudgement | None = None
    judge_failed: bool = False


@dataclass(frozen=True, slots=True)
class RewriteEvalParams:
    """Arguments of one eval pass."""

    repeat: int
    deadline_seconds: float
    max_tokens: int
    judge: RewriteJudge | None = None


@dataclass(slots=True)
class RewriteEvalResult:
    """Records plus the reason a pass stopped early, if it did."""

    records: list[RewriteRunRecord] = field(default_factory=list)
    stop_reason: str | None = None


@dataclass(frozen=True, slots=True)
class ModelEval:
    """One model's pass over the cases and the billable tokens it spent."""

    model: str
    result: RewriteEvalResult
    spent_billable: int


def _normalized(text: str) -> str:
    return " ".join(text.split())


_TONED = (VariantKind.SOFTER, VariantKind.FIRMER)


def _finish_set(
    case: RewriteCase,
    raw: tuple[Variant, ...],
) -> tuple[RunVariant, ...]:
    texts = {variant.kind: humanize_typography(case.draft, variant.text) for variant in raw}
    context = PolicyContext(
        goal=case.goal,
        rule_categories=frozenset(rule.category for rule in case.rules),
    )
    violations = check_variant_set(case.draft, texts, context)
    expected = {_normalized(item) for item in case.check_expected}
    return tuple(
        RunVariant(
            kind=kind,
            text=text,
            violations=tuple(item.rule for item in violations if item.kind is kind),
            check_ok=(
                _normalized(text) in expected
                if kind is VariantKind.CHECK and case.check_expected
                else None
            ),
            stem_leak=(
                variant_leaks_stems(
                    draft=case.draft, variants=(text,), stop_stems=case.forbidden_stems
                )
                if kind in _TONED and case.forbidden_stems
                else None
            ),
            closing_ask_ok=(
                variant_complies_with_closing_ask(text) if case.effect_pair is not None else None
            ),
        )
        for kind, text in texts.items()
    )


def _failed(
    case: RewriteCase,
    run_index: int,
    outcome: RunOutcome,
    billable_tokens: int,
    diagnostics: tuple[InvalidDiagnostic, ...] = (),
) -> RewriteRunRecord:
    """A call that produced no variants (refusal, invalid output, unavailable)."""
    return RewriteRunRecord(
        case_id=case.id,
        category=case.category,
        goal=case.goal,
        had_rules=bool(case.rules),
        run_index=run_index,
        outcome=outcome,
        variants=(),
        latency_ms=None,
        billable_tokens=billable_tokens,
        diagnostics=diagnostics,
    )


async def _call(
    gen: TextGenerator,
    case: RewriteCase,
    run_index: int,
    request: RewriteRequest,
) -> RewriteRunRecord:
    try:
        result = await gen.rewrite(request)
    except GenerationRefusedByProvider as exc:
        return _failed(case, run_index, RunOutcome.REFUSED, exc.usage.billable)
    except InvalidGenerationOutput as exc:
        diagnostics = tuple(
            InvalidDiagnostic(reason, finish_reason)
            for reason, finish_reason in zip(
                exc.labelled_reasons(), exc.finish_reasons, strict=True
            )
        )
        return _failed(case, run_index, RunOutcome.INVALID_OUTPUT, exc.usage.billable, diagnostics)
    filled, check_empty = fill_empty_check(case.draft, result.variants)
    outcome = RunOutcome.OK if result.safety is SafetyVerdict.OK else RunOutcome.NOT_OK_SAFETY
    return RewriteRunRecord(
        case_id=case.id,
        category=case.category,
        goal=case.goal,
        had_rules=bool(case.rules),
        run_index=run_index,
        outcome=outcome,
        variants=_finish_set(case, filled),
        latency_ms=result.meta.latency_ms,
        billable_tokens=result.meta.usage.billable,
        check_empty=check_empty,
    )


async def _judged(
    judge: RewriteJudge,
    case: RewriteCase,
    record: RewriteRunRecord,
    deadline_seconds: float,
) -> tuple[RewriteRunRecord, UnavailableKind | None]:
    """Add the judge's verdicts to a usable call; the second item is a stop kind, if any."""
    texts = {variant.kind: variant.text for variant in record.variants}
    request = JudgeRequest(
        draft=case.draft,
        goal=case.goal,
        rules=case.rules,
        softer=texts[VariantKind.SOFTER],
        firmer=texts[VariantKind.FIRMER],
        deadline_seconds=deadline_seconds,
    )
    try:
        observation = await judge.judge(request)
    except (GenerationRefusedByProvider, InvalidGenerationOutput, GenerationUnavailable) as exc:
        stop = (
            exc.kind if isinstance(exc, GenerationUnavailable) and exc.kind in _STOP_KINDS else None
        )
        failed = replace(
            record, judge_failed=True, billable_tokens=record.billable_tokens + exc.usage.billable
        )
        return failed, stop
    verdicts = derive_result(request, observation)
    judged = replace(
        record,
        billable_tokens=record.billable_tokens + verdicts.meta.usage.billable,
        judgement=RunJudgement(
            softer=verdicts.softer, firmer=verdicts.firmer, ladder_ok=verdicts.ladder_ok
        ),
    )
    return judged, None


def worst_case_judge_request(case: RewriteCase, deadline_seconds: float) -> JudgeRequest:
    """A worst-case-sized judge request: both variants as long as the length rule allows."""
    longest = (case.draft * 2)[: math.ceil(LENGTH_FACTOR * len(case.draft))]
    return JudgeRequest(
        draft=case.draft,
        goal=case.goal,
        rules=case.rules,
        softer=longest,
        firmer=longest,
        deadline_seconds=deadline_seconds,
    )


async def run_rewrite_eval(
    gen: TextGenerator,
    cases: list[RewriteCase],
    params: RewriteEvalParams,
    spend: SpendTracker,
) -> RewriteEvalResult:
    """Run every case ``repeat`` times, case by case; judge every usable call if asked.

    Stops before a call whose worst case (rewrite plus judge) would cross ``max_tokens``
    (``token_budget``) and at the first rate-limit or auth failure; never retries.
    """
    result = RewriteEvalResult()
    for case in cases:
        request = RewriteRequest(
            draft=case.draft,
            goal=case.goal,
            rules=case.rules,
            relationship=case.relationship,
            deadline_seconds=params.deadline_seconds,
        )
        worst_case = gen.max_billable(request)
        if params.judge is not None:
            worst_case += params.judge.max_billable(
                worst_case_judge_request(case, params.deadline_seconds)
            )
        for run_index in range(1, params.repeat + 1):
            if spend.spent_billable + worst_case > params.max_tokens:
                result.stop_reason = "token_budget"
                return result
            try:
                record = await _call(gen, case, run_index, request)
            except GenerationUnavailable as exc:
                spend.add(exc.usage.billable)
                result.records.append(
                    _failed(case, run_index, RunOutcome.UNAVAILABLE, exc.usage.billable)
                )
                if exc.kind in _STOP_KINDS:
                    result.stop_reason = exc.kind.value
                    return result
                continue
            stop: UnavailableKind | None = None
            if params.judge is not None and record.outcome is RunOutcome.OK:
                record, stop = await _judged(params.judge, case, record, params.deadline_seconds)
            spend.add(record.billable_tokens)
            result.records.append(record)
            if stop is not None:
                result.stop_reason = stop.value
                return result
    return result
