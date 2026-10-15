"""Eval-only port of the rewrite judge (ADR-0016 §8, §13).

The judge grades the two toned variants of one rewrite against a fixed rubric. Its result
carries verdicts and call metadata only, never text. Only the eval tools construct a judge.
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


@dataclass(frozen=True, slots=True)
class JudgeResult:
    """Per-slot verdicts and the pair verdict ``ladder_ok`` (never ``na``)."""

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

    async def judge(self, request: JudgeRequest) -> JudgeResult:
        """Grade ``softer`` and ``firmer`` of one rewrite."""

    def max_billable(self, request: JudgeRequest) -> int:
        """Worst-case billable tokens for ``request`` (input + output cap x attempts)."""
