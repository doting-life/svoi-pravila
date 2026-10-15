"""Eval-only port of the rewrite judge (ADR-0016 §8, §13).

The judge does not grade: it fills observations with evidence for the two toned variants of one
rewrite (``JudgeObservation``). The verdicts (``JudgeResult``) are derived from the observation
by code (``evals/judge_verdicts.py``). The quotes of an observation are text: they live in memory
for the local report only. ``JudgeResult`` carries verdicts and call metadata, never text. Only
the eval tools construct a judge.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from svoi_pravila.application.ports.generation import GenerationMeta, RuleContext
from svoi_pravila.domain.enums import RewriteGoal


class Verdict(StrEnum):
    """One criterion's value: ``na`` when the criterion does not apply."""

    YES = "yes"
    NO = "no"
    NA = "na"


@dataclass(frozen=True, slots=True)
class JudgeRequest:
    """One rewrite to grade: the author's draft, goal and rules, and the two variants."""

    draft: str
    goal: RewriteGoal | None
    rules: tuple[RuleContext, ...]
    softer: str
    firmer: str
    deadline_seconds: float


@dataclass(frozen=True, slots=True)
class SlotVerdicts:
    """Verdicts for one variant; ``goal_done`` and ``rule_done`` may be ``na``, the rest not."""

    clean: Verdict
    meaning_kept: Verdict
    invented: Verdict
    goal_done: Verdict
    rule_done: Verdict

    def __post_init__(self) -> None:
        if Verdict.NA in (self.clean, self.meaning_kept, self.invented):
            msg = "clean, meaning_kept and invented are never na"
            raise ValueError(msg)

    @property
    def all_good(self) -> bool:
        """True when no criterion is a defect; ``na`` of ``goal_done``/``rule_done`` is good."""
        return (
            self.clean is Verdict.YES
            and self.meaning_kept is Verdict.YES
            and self.invented is Verdict.NO
            and self.goal_done is not Verdict.NO
            and self.rule_done is not Verdict.NO
        )


class Position(StrEnum):
    """What became of the author's position in a variant."""

    SAME = "same"
    WEAKENED = "weakened"
    LOST = "lost"
    INVERTED = "inverted"
    OTHER_ACTOR = "other_actor"


class GoalBreach(StrEnum):
    """What stops a variant from performing the goal."""

    NONE = "none"
    HEDGED = "hedged"
    CONDITIONAL = "conditional"
    WISH_OR_QUESTION = "wish_or_question"
    EXCUSE = "excuse"
    SELF_BLAME = "self_blame"
    REPROACH = "reproach"
    PROHIBITION = "prohibition"
    ULTIMATUM = "ultimatum"


class RuleCheck(StrEnum):
    """Whether a variant follows the rules that apply to the draft."""

    FOLLOWED = "followed"
    BROKEN = "broken"
    NOT_APPLICABLE = "not_applicable"


class Warmer(StrEnum):
    """Which variant is warmer in tone."""

    SOFTER = "softer"
    FIRMER = "firmer"
    EQUAL = "equal"


@dataclass(frozen=True, slots=True)
class SlotObservation:
    """Evidence about one variant: quotes first, then the classifications."""

    rude: tuple[str, ...]
    added: tuple[str, ...]
    goal_sentence: str
    position: Position
    goal_breach: GoalBreach
    rule_check: RuleCheck


@dataclass(frozen=True, slots=True)
class PairObservation:
    """Evidence about the pair: the softeners and pressure of ``firmer``, and the warmer one."""

    firmer_softening: tuple[str, ...]
    firmer_pressure: tuple[str, ...]
    warmer: Warmer


@dataclass(frozen=True, slots=True)
class JudgeObservation:
    """The judge's answer: observations per variant and for the pair, plus call metadata."""

    softer: SlotObservation
    firmer: SlotObservation
    pair: PairObservation
    meta: GenerationMeta


@dataclass(frozen=True, slots=True)
class JudgeResult:
    """Per-slot verdicts and the pair verdict ``ladder_ok`` (never ``na``), derived by code."""

    softer: SlotVerdicts
    firmer: SlotVerdicts
    ladder_ok: Verdict
    meta: GenerationMeta

    def __post_init__(self) -> None:
        if self.ladder_ok is Verdict.NA:
            msg = "ladder_ok is never na"
            raise ValueError(msg)


class RewriteJudge(Protocol):
    """Port for the eval judge."""

    async def judge(self, request: JudgeRequest) -> JudgeObservation:
        """Observe ``softer`` and ``firmer`` of one rewrite."""

    def max_billable(self, request: JudgeRequest) -> int:
        """Worst-case billable tokens for ``request`` (input + output cap x attempts)."""
