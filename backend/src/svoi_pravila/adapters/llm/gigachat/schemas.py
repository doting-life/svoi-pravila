"""Private Pydantic models for GigaChat structured output (adapter-only)."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from svoi_pravila.domain.text import RULE_TEXT_MAX_CHARS

_HypothesisText = Annotated[str, Field(max_length=1000)]


class VariantKindOut(StrEnum):
    """Kinds a decode reply can carry (decode has no «check» variant)."""

    SOFTER = "softer"
    FIRMER = "firmer"


class SafetyOut(StrEnum):
    OK = "ok"
    CRISIS = "crisis"
    REFUSE_MANIPULATION = "refuse_manipulation"


class VariantOut(BaseModel):
    """Decode-only variant item (array with kind). Rewrite uses fixed slots."""

    text: str = Field(min_length=1, max_length=1000)
    kind: VariantKindOut


class RewriteOut(BaseModel):
    """Fixed three-slot rewrite output when the request carries rules.

    Slot order is the generation order: ``firmer`` is written before ``softer``.
    """

    check: str = Field(max_length=1000)
    firmer: str = Field(max_length=1000)
    softer: str = Field(max_length=1000)
    applied_rule_indexes: list[int]
    safety: SafetyOut


class RewriteOutNoRules(BaseModel):
    """Rewrite slots when the request has no rules (indexes omitted from the schema)."""

    model_config = ConfigDict(extra="forbid")

    check: str = Field(max_length=1000)
    firmer: str = Field(max_length=1000)
    softer: str = Field(max_length=1000)
    safety: SafetyOut


class DecodeOut(BaseModel):
    hypotheses: list[_HypothesisText] = Field(default_factory=list, max_length=3)
    underlying_request: str = Field(default="", max_length=1000)
    variants: list[VariantOut] = Field(default_factory=list, max_length=2)
    applied_rule_indexes: list[int]
    safety: SafetyOut


class SuggestRuleVerdictOut(StrEnum):
    OK = "ok"
    NONE = "none"


class RuleCategoryOut(StrEnum):
    TABOO_TOPIC = "taboo_topic"
    HOW_TO_ASK = "how_to_ask"
    APOLOGY = "apology"
    CONFLICT_PROTOCOL = "conflict_protocol"
    OTHER = "other"


class SuggestRuleOut(BaseModel):
    verdict: SuggestRuleVerdictOut
    category: RuleCategoryOut | None = None
    text: str | None = Field(default=None, max_length=RULE_TEXT_MAX_CHARS)
