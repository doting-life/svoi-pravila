"""Private Pydantic models for GigaChat structured output (adapter-only)."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from svoi_pravila.domain.text import RULE_TEXT_MAX_CHARS

_HypothesisText = Annotated[str, Field(max_length=1000)]


class FirmnessOut(StrEnum):
    GENTLE = "gentle"
    BALANCED = "balanced"
    FIRM = "firm"


class SafetyOut(StrEnum):
    OK = "ok"
    CRISIS = "crisis"
    REFUSE_MANIPULATION = "refuse_manipulation"


class VariantOut(BaseModel):
    """Decode-only variant item (array with firmness). Soften/help_say use slots."""

    text: str = Field(min_length=1, max_length=1000)
    firmness: FirmnessOut


class SoftenOut(BaseModel):
    """Fixed three-slot soften output when the request carries rules."""

    gentle: str = Field(max_length=1000)
    balanced: str = Field(max_length=1000)
    firm: str = Field(max_length=1000)
    applied_rule_indexes: list[int]
    safety: SafetyOut


class SoftenOutNoRules(BaseModel):
    """Soften slots when the request has no rules (indexes omitted from the schema)."""

    model_config = ConfigDict(extra="forbid")

    gentle: str = Field(max_length=1000)
    balanced: str = Field(max_length=1000)
    firm: str = Field(max_length=1000)
    safety: SafetyOut


class HelpSayOut(BaseModel):
    """Fixed three-slot help-say output when the request carries rules."""

    gentle: str = Field(max_length=1000)
    balanced: str = Field(max_length=1000)
    firm: str = Field(max_length=1000)
    applied_rule_indexes: list[int]
    safety: SafetyOut


class HelpSayOutNoRules(BaseModel):
    """Help-say slots when the request has no rules (indexes omitted from the schema)."""

    model_config = ConfigDict(extra="forbid")

    gentle: str = Field(max_length=1000)
    balanced: str = Field(max_length=1000)
    firm: str = Field(max_length=1000)
    safety: SafetyOut


class DecodeOut(BaseModel):
    hypotheses: list[_HypothesisText] = Field(default_factory=list, max_length=3)
    underlying_request: str = Field(default="", max_length=1000)
    variants: list[VariantOut] = Field(default_factory=list, max_length=3)
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
