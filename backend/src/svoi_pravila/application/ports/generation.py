"""Scenario-level text generation port (rewrite, decode_stream, suggest_rule)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from svoi_pravila.domain.enums import RelationshipKind, RewriteGoal, RuleCategory, VariantKind
from svoi_pravila.domain.text import INCOMING_TEXT_MAX_CHARS, RuleText

BOUNDED_TEXT_MIN = 1
BOUNDED_TEXT_MAX = INCOMING_TEXT_MAX_CHARS


def _validate_bounded_text(name: str, value: str) -> None:
    length = len(value)
    if length < BOUNDED_TEXT_MIN or length > BOUNDED_TEXT_MAX:
        msg = f"{name} length must be between {BOUNDED_TEXT_MIN} and {BOUNDED_TEXT_MAX}"
        raise ValueError(msg)


class SafetyVerdict(StrEnum):
    """Safety classification returned with a generation result."""

    OK = "ok"
    CRISIS = "crisis"
    REFUSE_MANIPULATION = "refuse_manipulation"


@dataclass(frozen=True, slots=True)
class RuleContext:
    """One effective agreement supplied as generation context."""

    category: RuleCategory
    text: str
    effective_since: datetime


@dataclass(frozen=True, slots=True)
class AppliedRuleView:
    """Rule context selected by validated ``applied_rule_indexes``."""

    index: int
    category: RuleCategory
    text: str
    effective_since: datetime


@dataclass(frozen=True, slots=True)
class RewriteRequest:
    """Input for rewriting a draft into check, softer and firmer variants."""

    draft: str
    goal: RewriteGoal | None
    rules: tuple[RuleContext, ...]
    relationship: RelationshipKind
    deadline_seconds: float

    def __post_init__(self) -> None:
        _validate_bounded_text("draft", self.draft)


@dataclass(frozen=True, slots=True)
class DecodeRequest:
    """Input for decoding an incoming message."""

    incoming: str
    rules: tuple[RuleContext, ...]
    relationship: RelationshipKind
    deadline_seconds: float

    def __post_init__(self) -> None:
        _validate_bounded_text("incoming", self.incoming)


@dataclass(frozen=True, slots=True)
class SuggestRuleRequest:
    """Input for suggesting a rule from an incoming message."""

    incoming: str
    rules: tuple[RuleContext, ...]
    relationship: RelationshipKind
    deadline_seconds: float

    def __post_init__(self) -> None:
        _validate_bounded_text("incoming", self.incoming)


@dataclass(frozen=True, slots=True)
class SuggestRuleProposed:
    """Model proposed a rule candidate."""

    category: RuleCategory
    text: RuleText
    meta: GenerationMeta


@dataclass(frozen=True, slots=True)
class SuggestRuleNothing:
    """Model decided nothing should be recorded as a rule."""

    meta: GenerationMeta


SuggestRuleResult = SuggestRuleProposed | SuggestRuleNothing


@dataclass(frozen=True, slots=True)
class Variant:
    """One suggested text of a fixed kind."""

    text: str
    kind: VariantKind


@dataclass(frozen=True, slots=True)
class TokenUsage:
    """C0 token accounting for one generation call (or partial failure)."""

    input: int = 0
    output: int = 0
    precached: int = 0

    @property
    def billable(self) -> int:
        """Billable tokens: prompt after cache subtraction + completion.

        GigaChat documents ``prompt_tokens`` as already net of cache and
        ``total_tokens`` as the billable total after subtracting cached tokens
        (https://developers.sber.ru/docs/ru/gigachat/guides/counting-tokens).
        """
        return self.input + self.output

    def __add__(self, other: TokenUsage) -> TokenUsage:
        return TokenUsage(
            input=self.input + other.input,
            output=self.output + other.output,
            precached=self.precached + other.precached,
        )


@dataclass(frozen=True, slots=True)
class GenerationMeta:
    """Non-text metadata about a generation call."""

    model: str
    prompt_version: str
    latency_ms: int
    attempts: int
    usage: TokenUsage


@dataclass(frozen=True, slots=True)
class RewriteResult:
    """Structured rewrite response: variants in order check, softer, firmer (empty if not ok)."""

    variants: tuple[Variant, ...]
    applied_rule_indexes: tuple[int, ...]
    safety: SafetyVerdict
    meta: GenerationMeta


@dataclass(frozen=True, slots=True)
class DecodeResult:
    """Structured decode response."""

    hypotheses: tuple[str, ...]
    underlying_request: str
    variants: tuple[Variant, ...]
    applied_rule_indexes: tuple[int, ...]
    safety: SafetyVerdict
    meta: GenerationMeta


@dataclass(frozen=True, slots=True)
class AnalysisChunk:
    """Streaming free-text analysis fragment for decode."""

    text: str


@dataclass(frozen=True, slots=True)
class DecodeCompleted:
    """Final structured decode result after streaming analysis."""

    analysis: str
    result: DecodeResult
    applied_rules: tuple[AppliedRuleView, ...] = ()


DecodeEvent = AnalysisChunk | DecodeCompleted


GenerationRequest = RewriteRequest | DecodeRequest | SuggestRuleRequest


class TextGenerator(Protocol):
    """Port for scenario-level generation."""

    async def rewrite(self, request: RewriteRequest) -> RewriteResult:
        """Produce check, softer and firmer variants of a draft in one call."""

    def decode_stream(self, request: DecodeRequest) -> AsyncIterator[DecodeEvent]:
        """Stream decode analysis chunks, then a completed structured result."""

    async def suggest_rule(self, request: SuggestRuleRequest) -> SuggestRuleResult:
        """Propose a rule candidate from an incoming message, or none."""

    def max_billable(self, request: GenerationRequest) -> int:
        """Worst-case billable tokens for ``request`` (input + output caps x attempts)."""
