"""Pure validation helpers for GigaChat generation outputs."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

import structlog

from svoi_pravila.adapters.llm.gigachat.schemas import (
    DecodeOut,
    SuggestRuleOut,
    SuggestRuleVerdictOut,
    VariantOut,
)
from svoi_pravila.application.errors import InvalidGenerationOutput, InvalidOutputReason
from svoi_pravila.application.ports.generation import (
    DecodeResult,
    GenerationMeta,
    RuleContext,
    SafetyVerdict,
    SuggestRuleNothing,
    SuggestRuleProposed,
    SuggestRuleResult,
    TokenUsage,
    Variant,
)
from svoi_pravila.domain.enums import Firmness, RuleCategory
from svoi_pravila.domain.errors import InvalidValueError
from svoi_pravila.domain.text import RuleText

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

logger = structlog.get_logger(__name__)


def output_token_cap(max_chars: int) -> int:
    """Convert a validated character budget into a request max_tokens cap."""
    return math.ceil(max_chars / CHARS_PER_TOKEN) + JSON_OVERHEAD_TOKENS


MAX_TOKENS_SOFTEN = output_token_cap(MAX_VARIANTS * MAX_VARIANT_CHARS)
MAX_TOKENS_HELP_SAY = MAX_TOKENS_SOFTEN
MAX_TOKENS_ANALYSIS = output_token_cap(MAX_ANALYSIS_CHARS)
MAX_TOKENS_DECODE = output_token_cap(
    MAX_VARIANTS * MAX_VARIANT_CHARS + MAX_HYPOTHESES * MAX_VARIANT_CHARS + MAX_VARIANT_CHARS
)
MAX_RULE_CHARS = 280
MAX_TOKENS_SUGGEST = output_token_cap(MAX_RULE_CHARS)


@dataclass(frozen=True, slots=True)
class VariantValidation:
    """Parameters for validating a variant list against port invariants."""

    applied: list[int]
    safety_raw: str
    rule_count: int
    min_variants: int
    max_variants: int
    require_all_firmness: bool
    usage: TokenUsage
    attempts: int
    model: str
    prompt_version: str
    operation: str


def invalid(
    reason: InvalidOutputReason,
    *,
    usage: TokenUsage,
    attempts: int,
    model: str,
    prompt_version: str,
) -> InvalidGenerationOutput:
    """Build a single-reason InvalidGenerationOutput."""
    return InvalidGenerationOutput(
        (reason,),
        usage=usage,
        attempts=attempts,
        model=model,
        prompt_version=prompt_version,
    )


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
) -> tuple[tuple[Variant, ...], tuple[int, ...], SafetyVerdict]:
    """Validate variant list and safety against port invariants."""

    def fail(reason: InvalidOutputReason) -> InvalidGenerationOutput:
        return invalid(
            reason,
            usage=spec.usage,
            attempts=spec.attempts,
            model=spec.model,
            prompt_version=spec.prompt_version,
        )

    try:
        # StrEnum constructor raises ValueError for an unknown member.
        safety = SafetyVerdict(spec.safety_raw)
    except ValueError as exc:
        raise fail(InvalidOutputReason.SCHEMA_VIOLATION) from exc

    for idx in spec.applied:
        if idx < 0 or idx >= spec.rule_count:
            raise fail(InvalidOutputReason.RULE_INDEX_OUT_OF_RANGE)

    if safety is not SafetyVerdict.OK:
        if variants_raw:
            logger.info(
                "non_ok_payload_dropped",
                operation=spec.operation,
                verdict=safety.value,
            )
        return (), tuple(spec.applied), safety

    if not (spec.min_variants <= len(variants_raw) <= spec.max_variants):
        raise fail(InvalidOutputReason.VARIANT_COUNT)

    variants: list[Variant] = []
    seen: set[Firmness] = set()
    for item in variants_raw:
        dirty = text_reason(item.text)
        if dirty is not None:
            raise fail(dirty)
        try:
            # StrEnum constructor raises ValueError for an unknown member.
            firmness = Firmness(item.firmness.value)
        except ValueError as exc:
            raise fail(InvalidOutputReason.SCHEMA_VIOLATION) from exc
        if firmness in seen:
            raise fail(InvalidOutputReason.FIRMNESS_SET)
        seen.add(firmness)
        variants.append(Variant(text=item.text, firmness=firmness))

    if spec.require_all_firmness and seen != set(Firmness):
        raise fail(InvalidOutputReason.FIRMNESS_SET)

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
            usage=usage,
            attempts=attempts,
            model=meta.model,
            prompt_version=meta.prompt_version,
            operation="decode_stream",
        ),
    )

    def fail(reason: InvalidOutputReason) -> InvalidGenerationOutput:
        return invalid(
            reason,
            usage=usage,
            attempts=attempts,
            model=meta.model,
            prompt_version=meta.prompt_version,
        )

    if ok:
        if not (1 <= len(parsed.hypotheses) <= MAX_HYPOTHESES):
            raise fail(InvalidOutputReason.HYPOTHESIS_COUNT)
        request_reason = text_reason(parsed.underlying_request.strip())
        if request_reason is not None:
            raise fail(request_reason)
        for hyp in parsed.hypotheses:
            hyp_reason = text_reason(hyp)
            if hyp_reason is not None:
                raise fail(hyp_reason)
    if not ok:
        if parsed.hypotheses or parsed.underlying_request.strip():
            logger.info(
                "non_ok_payload_dropped",
                operation="decode_stream",
                verdict=safety.value,
            )
        return DecodeResult(
            hypotheses=(),
            underlying_request="",
            variants=variants,
            applied_rule_indexes=indexes,
            safety=safety,
            meta=meta,
        )
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


def to_suggest_rule_result(parsed: SuggestRuleOut, meta: GenerationMeta) -> SuggestRuleResult:
    """Validate structured suggest_rule output into a port result."""

    def fail(reason: InvalidOutputReason) -> InvalidGenerationOutput:
        return invalid(
            reason,
            usage=meta.usage,
            attempts=meta.attempts,
            model=meta.model,
            prompt_version=meta.prompt_version,
        )

    if parsed.verdict is SuggestRuleVerdictOut.NONE:
        if parsed.category is not None or parsed.text is not None:
            raise fail(InvalidOutputReason.SCHEMA_VIOLATION)
        return SuggestRuleNothing(meta=meta)
    if parsed.category is None or parsed.text is None:
        raise fail(InvalidOutputReason.SCHEMA_VIOLATION)
    text = parsed.text.strip()
    if not text:
        raise fail(InvalidOutputReason.EMPTY_TEXT)
    if len(text) > MAX_RULE_CHARS:
        raise fail(InvalidOutputReason.TEXT_TOO_LONG)
    if _URL_RE.search(text) is not None:
        raise fail(InvalidOutputReason.URL_IN_TEXT)
    if _FENCE_RE.search(text) is not None:
        raise fail(InvalidOutputReason.MARKUP_FENCE)
    try:
        rule_text = RuleText(text)
    except InvalidValueError as exc:
        raise fail(InvalidOutputReason.EMPTY_TEXT) from exc
    try:
        category = RuleCategory(parsed.category.value)
    except ValueError as exc:
        raise fail(InvalidOutputReason.SCHEMA_VIOLATION) from exc
    return SuggestRuleProposed(category=category, text=rule_text, meta=meta)


def untrusted_texts(*parts: str, rules: tuple[RuleContext, ...] = ()) -> tuple[str, ...]:
    """Collect all untrusted strings for boundary allocation."""
    return (*parts, *(rule.text for rule in rules))


def last_reason(exc: InvalidGenerationOutput) -> InvalidOutputReason:
    """Return the reason for the latest attempt (reasons is never empty)."""
    return exc.reasons[-1]
