"""Pure validation helpers for GigaChat generation outputs."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

from svoi_pravila.adapters.llm.gigachat.schemas import DecodeOut, VariantOut
from svoi_pravila.application.errors import InvalidGenerationOutput, InvalidOutputReason
from svoi_pravila.application.ports.generation import (
    DecodeResult,
    Firmness,
    GenerationMeta,
    RuleContext,
    SafetyVerdict,
    TokenUsage,
    Variant,
)

_URL_RE = re.compile(r"https?://|www\.", re.IGNORECASE)
_FENCE_RE = re.compile(r"```")
MAX_VARIANT_CHARS = 1000
MAX_HYPOTHESES = 3
MAX_ANALYSIS_CHARS = 2000
MAX_VARIANTS = 3
# Docs: average 3-4 characters per token; 3 is conservative so a max-valid
# payload is never truncated by max_tokens.
CHARS_PER_TOKEN = 3
JSON_OVERHEAD_TOKENS = 128


def output_token_cap(max_chars: int) -> int:
    """Convert a validated character budget into a request max_tokens cap."""
    return math.ceil(max_chars / CHARS_PER_TOKEN) + JSON_OVERHEAD_TOKENS


MAX_TOKENS_SOFTEN = output_token_cap(MAX_VARIANTS * MAX_VARIANT_CHARS)
MAX_TOKENS_HELP_SAY = MAX_TOKENS_SOFTEN
MAX_TOKENS_ANALYSIS = output_token_cap(MAX_ANALYSIS_CHARS)
MAX_TOKENS_DECODE = output_token_cap(
    MAX_VARIANTS * MAX_VARIANT_CHARS + MAX_HYPOTHESES * MAX_VARIANT_CHARS + MAX_VARIANT_CHARS
)


@dataclass(frozen=True, slots=True)
class VariantValidation:
    """Parameters for validating a variant list against port invariants."""

    applied: list[int]
    safety_raw: str
    rule_count: int
    min_variants: int
    max_variants: int
    require_all_firmness: bool


def invalid(
    reason: InvalidOutputReason,
    *,
    usage: TokenUsage,
    attempts: int,
) -> InvalidGenerationOutput:
    """Build a single-reason InvalidGenerationOutput."""
    return InvalidGenerationOutput((reason,), usage=usage, attempts=attempts)


def text_reason(text: str) -> InvalidOutputReason | None:
    """Return a content defect reason for a short variant/hypothesis string."""
    if not text:
        return InvalidOutputReason.EMPTY_TEXT
    if len(text) > MAX_VARIANT_CHARS:
        return InvalidOutputReason.TEXT_TOO_LONG
    if _URL_RE.search(text) is not None:
        return InvalidOutputReason.URL_IN_TEXT
    if _FENCE_RE.search(text) is not None:
        return InvalidOutputReason.MARKUP_FENCE
    return None


def analysis_reason(text: str) -> InvalidOutputReason | None:
    """Return a content defect reason for phase-A analysis text."""
    if not text.strip():
        return InvalidOutputReason.EMPTY_TEXT
    if len(text) > MAX_ANALYSIS_CHARS:
        return InvalidOutputReason.ANALYSIS_TOO_LONG
    if _URL_RE.search(text) is not None:
        return InvalidOutputReason.URL_IN_TEXT
    if _FENCE_RE.search(text) is not None:
        return InvalidOutputReason.MARKUP_FENCE
    return None


def validate_variants(
    variants_raw: list[VariantOut],
    spec: VariantValidation,
    *,
    usage: TokenUsage,
    attempts: int,
) -> tuple[tuple[Variant, ...], tuple[int, ...], SafetyVerdict]:
    """Validate variant list and safety against port invariants."""
    try:
        # StrEnum constructor raises ValueError for an unknown member.
        safety = SafetyVerdict(spec.safety_raw)
    except ValueError as exc:
        raise invalid(InvalidOutputReason.SCHEMA_VIOLATION, usage=usage, attempts=attempts) from exc

    for idx in spec.applied:
        if idx < 0 or idx >= spec.rule_count:
            raise invalid(
                InvalidOutputReason.RULE_INDEX_OUT_OF_RANGE,
                usage=usage,
                attempts=attempts,
            )

    if safety is not SafetyVerdict.OK:
        if variants_raw:
            raise invalid(
                InvalidOutputReason.NON_OK_WITH_PAYLOAD,
                usage=usage,
                attempts=attempts,
            )
        return (), tuple(spec.applied), safety

    if not (spec.min_variants <= len(variants_raw) <= spec.max_variants):
        raise invalid(InvalidOutputReason.VARIANT_COUNT, usage=usage, attempts=attempts)

    variants: list[Variant] = []
    seen: set[Firmness] = set()
    for item in variants_raw:
        dirty = text_reason(item.text)
        if dirty is not None:
            raise invalid(dirty, usage=usage, attempts=attempts)
        try:
            # StrEnum constructor raises ValueError for an unknown member.
            firmness = Firmness(item.firmness.value)
        except ValueError as exc:
            raise invalid(
                InvalidOutputReason.SCHEMA_VIOLATION, usage=usage, attempts=attempts
            ) from exc
        if firmness in seen:
            raise invalid(InvalidOutputReason.FIRMNESS_SET, usage=usage, attempts=attempts)
        seen.add(firmness)
        variants.append(Variant(text=item.text, firmness=firmness))

    if spec.require_all_firmness and seen != set(Firmness):
        raise invalid(InvalidOutputReason.FIRMNESS_SET, usage=usage, attempts=attempts)

    return tuple(variants), tuple(spec.applied), safety


def to_decode_result(parsed: DecodeOut, meta: GenerationMeta, *, rule_count: int) -> DecodeResult:
    """Map DecodeOut to DecodeResult with content checks."""
    usage = meta.usage
    attempts = meta.attempts
    safety = SafetyVerdict(parsed.safety.value)
    ok = safety is SafetyVerdict.OK
    variants, indexes, safety = validate_variants(
        parsed.variants,
        VariantValidation(
            applied=list(parsed.applied_rule_indexes),
            safety_raw=parsed.safety.value,
            rule_count=rule_count,
            min_variants=3 if ok else 0,
            max_variants=3,
            require_all_firmness=ok,
        ),
        usage=usage,
        attempts=attempts,
    )
    if ok:
        if not (1 <= len(parsed.hypotheses) <= MAX_HYPOTHESES):
            raise invalid(InvalidOutputReason.HYPOTHESIS_COUNT, usage=usage, attempts=attempts)
        request_reason = text_reason(parsed.underlying_request.strip())
        if request_reason is not None:
            raise invalid(request_reason, usage=usage, attempts=attempts)
        for hyp in parsed.hypotheses:
            hyp_reason = text_reason(hyp)
            if hyp_reason is not None:
                raise invalid(hyp_reason, usage=usage, attempts=attempts)
    elif parsed.hypotheses or parsed.underlying_request.strip():
        raise invalid(InvalidOutputReason.NON_OK_WITH_PAYLOAD, usage=usage, attempts=attempts)
    return DecodeResult(
        hypotheses=tuple(parsed.hypotheses),
        underlying_request=parsed.underlying_request,
        variants=variants,
        applied_rule_indexes=indexes,
        safety=safety,
        meta=meta,
    )


def rules_text(rules: tuple[RuleContext, ...]) -> str:
    """Format rules for the untrusted payload."""
    if not rules:
        return "(none)"
    lines: list[str] = []
    for index, rule in enumerate(rules):
        lines.append(
            f"[{index}] category={rule.category.value}; since={rule.effective_since.isoformat()}"
        )
        lines.append(rule.text)
    return "\n".join(lines)


def untrusted_texts(*parts: str, rules: tuple[RuleContext, ...] = ()) -> tuple[str, ...]:
    """Collect all untrusted strings for boundary allocation."""
    return (*parts, *(rule.text for rule in rules))


def last_reason(exc: InvalidGenerationOutput) -> InvalidOutputReason:
    """Return the reason for the latest attempt (reasons is never empty)."""
    return exc.reasons[-1]
