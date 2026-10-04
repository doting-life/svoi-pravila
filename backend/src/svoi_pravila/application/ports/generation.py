"""Scenario-level text generation port (soften, help-say, decode_stream)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from svoi_pravila.domain.enums import Firmness, RelationshipKind, RuleCategory

BOUNDED_TEXT_MIN = 1
BOUNDED_TEXT_MAX = 4000


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


class HelpSayIntent(StrEnum):
    """Intent for the help-say scenario."""

    DECLINE = "decline"
    SET_BOUNDARY = "set_boundary"
    ADMIT_FAULT = "admit_fault"
    RECONNECT_AFTER_CONFLICT = "reconnect_after_conflict"
    OTHER = "other"


@dataclass(frozen=True, slots=True)
class RuleContext:
    """One effective agreement supplied as generation context."""

    category: RuleCategory
    text: str
    effective_since: datetime


@dataclass(frozen=True, slots=True)
class SoftenRequest:
    """Input for softening a draft message."""

    draft: str
    rules: tuple[RuleContext, ...]
    relationship: RelationshipKind
    deadline_seconds: float

    def __post_init__(self) -> None:
        _validate_bounded_text("draft", self.draft)


@dataclass(frozen=True, slots=True)
class HelpSayRequest:
    """Input for composing a difficult message from an intent."""

    intent: HelpSayIntent
    details: str
    rules: tuple[RuleContext, ...]
    relationship: RelationshipKind
    deadline_seconds: float

    def __post_init__(self) -> None:
        _validate_bounded_text("details", self.details)


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
class Variant:
    """One suggested reply with firmness."""

    text: str
    firmness: Firmness


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
class SoftenResult:
    """Structured soften response."""

    variants: tuple[Variant, ...]
    applied_rule_indexes: tuple[int, ...]
    safety: SafetyVerdict
    meta: GenerationMeta


@dataclass(frozen=True, slots=True)
class HelpSayResult:
    """Structured help-say response."""

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


DecodeEvent = AnalysisChunk | DecodeCompleted


class TextGenerator(Protocol):
    """Port for scenario-level generation."""

    async def soften(self, request: SoftenRequest) -> SoftenResult:
        """Produce softened variants of a draft."""

    async def help_say(self, request: HelpSayRequest) -> HelpSayResult:
        """Produce variants for a difficult message."""

    def decode_stream(self, request: DecodeRequest) -> AsyncIterator[DecodeEvent]:
        """Stream decode analysis chunks, then a completed structured result."""
