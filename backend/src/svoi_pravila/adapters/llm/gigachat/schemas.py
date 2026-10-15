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
    """Fixed three-slot rewrite output when the request carries rules."""

    check: str = Field(max_length=1000)
    softer: str = Field(max_length=1000)
    firmer: str = Field(max_length=1000)
    applied_rule_indexes: list[int]
    safety: SafetyOut


class RewriteOutNoRules(BaseModel):
    """Rewrite slots when the request has no rules (indexes omitted from the schema)."""

    model_config = ConfigDict(extra="forbid")

    check: str = Field(max_length=1000)
    softer: str = Field(max_length=1000)
    firmer: str = Field(max_length=1000)
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


class PositionOut(StrEnum):
    SAME = "same"
    WEAKENED = "weakened"
    LOST = "lost"
    INVERTED = "inverted"
    OTHER_ACTOR = "other_actor"


class GoalBreachOut(StrEnum):
    NONE = "none"
    HEDGED = "hedged"
    CONDITIONAL = "conditional"
    WISH_OR_QUESTION = "wish_or_question"
    EXCUSE = "excuse"
    SELF_BLAME = "self_blame"
    REPROACH = "reproach"
    PROHIBITION = "prohibition"
    ULTIMATUM = "ultimatum"


class RuleCheckOut(StrEnum):
    FOLLOWED = "followed"
    BROKEN = "broken"
    NOT_APPLICABLE = "not_applicable"


class WarmerOut(StrEnum):
    SOFTER = "softer"
    FIRMER = "firmer"
    EQUAL = "equal"


JUDGE_MAX_QUOTES = 4
JUDGE_MAX_QUOTE_CHARS = 120
JUDGE_MAX_SENTENCE_CHARS = 300

_Quotes = Annotated[
    list[Annotated[str, Field(min_length=1, max_length=JUDGE_MAX_QUOTE_CHARS)]],
    Field(max_length=JUDGE_MAX_QUOTES),
]


class SlotJudgeOut(BaseModel):
    """Observations for one variant (rubric ``judge_rewrite@v3``): quotes, then enums."""

    model_config = ConfigDict(extra="forbid")

    rude: _Quotes
    added: _Quotes
    position: PositionOut
    goal_sentence: str = Field(max_length=JUDGE_MAX_SENTENCE_CHARS)
    goal_breach: GoalBreachOut
    rule_evidence: _Quotes
    rule_check: RuleCheckOut


class JudgeOut(BaseModel):
    """Judge output: observations per toned variant, then the pair observations."""

    model_config = ConfigDict(extra="forbid")

    softer: SlotJudgeOut
    firmer: SlotJudgeOut
    firmer_softening: _Quotes
    firmer_pressure: _Quotes
    warmer: WarmerOut
