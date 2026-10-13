"""Run the rewrite eval: N calls per case, policy checked on every variant."""

from __future__ import annotations

from dataclasses import dataclass, field
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
)
from svoi_pravila.application.rewrite_variants import fill_empty_check
from svoi_pravila.benchmarks.runner import SpendTracker
from svoi_pravila.domain.enums import VariantKind
from svoi_pravila.domain.typography import humanize_typography
from svoi_pravila.domain.variant_policy import PolicyRule, check_variant_policy
from svoi_pravila.evals.rewrite_cases import RewriteCase

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
    """One variant after typography, with the policy rules it broke."""

    kind: VariantKind
    text: str
    violations: tuple[PolicyRule, ...]
    check_ok: bool | None = None


@dataclass(frozen=True, slots=True)
class InvalidDiagnostic:
    """C0 diagnostics of one failed attempt: ``reason[:slot]`` and the provider finish_reason."""

    reason: str
    finish_reason: str | None


@dataclass(frozen=True, slots=True)
class RewriteRunRecord:
    """One call for one case. Texts exist only for the local report."""

    case_id: str
    run_index: int
    outcome: RunOutcome
    variants: tuple[RunVariant, ...]
    latency_ms: int | None
    billable_tokens: int
    diagnostics: tuple[InvalidDiagnostic, ...] = ()
    check_empty: bool = False


@dataclass(frozen=True, slots=True)
class RewriteEvalParams:
    """Arguments of one eval pass."""

    repeat: int
    deadline_seconds: float
    max_tokens: int


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


def _finish(case: RewriteCase, kind: VariantKind, raw_text: str) -> RunVariant:
    text = humanize_typography(case.draft, raw_text)
    rules = tuple(violation.rule for violation in check_variant_policy(case.draft, kind, text))
    check_ok = (
        _normalized(text) in {_normalized(expected) for expected in case.check_expected}
        if kind is VariantKind.CHECK and case.check_expected
        else None
    )
    return RunVariant(kind=kind, text=text, violations=rules, check_ok=check_ok)


async def _call(
    gen: TextGenerator, case: RewriteCase, run_index: int, request: RewriteRequest
) -> RewriteRunRecord:
    try:
        result = await gen.rewrite(request)
    except GenerationRefusedByProvider as exc:
        return RewriteRunRecord(
            case.id, run_index, RunOutcome.REFUSED, (), None, exc.usage.billable
        )
    except InvalidGenerationOutput as exc:
        diagnostics = tuple(
            InvalidDiagnostic(reason, finish_reason)
            for reason, finish_reason in zip(
                exc.labelled_reasons(), exc.finish_reasons, strict=True
            )
        )
        return RewriteRunRecord(
            case.id,
            run_index,
            RunOutcome.INVALID_OUTPUT,
            (),
            None,
            exc.usage.billable,
            diagnostics,
        )
    filled, check_empty = fill_empty_check(case.draft, result.variants)
    variants = tuple(_finish(case, variant.kind, variant.text) for variant in filled)
    outcome = RunOutcome.OK if result.safety is SafetyVerdict.OK else RunOutcome.NOT_OK_SAFETY
    return RewriteRunRecord(
        case.id,
        run_index,
        outcome,
        variants,
        result.meta.latency_ms,
        result.meta.usage.billable,
        check_empty=check_empty,
    )


async def run_rewrite_eval(
    gen: TextGenerator,
    cases: list[RewriteCase],
    params: RewriteEvalParams,
    spend: SpendTracker,
) -> RewriteEvalResult:
    """Run every case ``repeat`` times, case by case.

    Stops before a call whose worst case would cross ``max_tokens`` (``token_budget``)
    and at the first rate-limit or auth failure; never retries.
    """
    result = RewriteEvalResult()
    for case in cases:
        request = RewriteRequest(
            draft=case.draft,
            goal=case.goal,
            rules=(),
            relationship=case.relationship,
            deadline_seconds=params.deadline_seconds,
        )
        worst_case = gen.max_billable(request)
        for run_index in range(1, params.repeat + 1):
            if spend.spent_billable + worst_case > params.max_tokens:
                result.stop_reason = "token_budget"
                return result
            try:
                record = await _call(gen, case, run_index, request)
            except GenerationUnavailable as exc:
                spend.add(exc.usage.billable)
                result.records.append(
                    RewriteRunRecord(
                        case.id, run_index, RunOutcome.UNAVAILABLE, (), None, exc.usage.billable
                    )
                )
                if exc.kind in _STOP_KINDS:
                    result.stop_reason = exc.kind.value
                    return result
                continue
            spend.add(record.billable_tokens)
            result.records.append(record)
    return result
