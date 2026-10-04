"""Private Pydantic models for GigaChat structured output (adapter-only)."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, Field

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
    text: str = Field(min_length=1, max_length=1000)
    firmness: FirmnessOut


class SoftenOut(BaseModel):
    variants: list[VariantOut] = Field(max_length=3)
    applied_rule_indexes: list[int]
    safety: SafetyOut


class HelpSayOut(BaseModel):
    variants: list[VariantOut] = Field(max_length=3)
    applied_rule_indexes: list[int]
    safety: SafetyOut


class DecodeOut(BaseModel):
    hypotheses: list[_HypothesisText] = Field(default_factory=list, max_length=3)
    underlying_request: str = Field(default="", max_length=1000)
    variants: list[VariantOut] = Field(default_factory=list, max_length=3)
    applied_rule_indexes: list[int]
    safety: SafetyOut
