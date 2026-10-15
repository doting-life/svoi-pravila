"""Eval-only port of the clean judge (ADR-0016 §8, §13).

The judge looks for rude fragments in the two toned variants of one rewrite
(``JudgeObservation``). The verdicts (``JudgeResult``) are derived from the observation by code
(``evals/judge_verdicts.py``). The quotes of an observation are text: they live in memory for the
local report only. ``JudgeResult`` carries verdicts and call metadata, never text. Only the eval
tools construct a judge.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from svoi_pravila.application.ports.generation import GenerationMeta


class Verdict(StrEnum):
    """The value of ``clean``: ``yes`` when a variant has no vulgar or rude language."""

    YES = "yes"
    NO = "no"


@dataclass(frozen=True, slots=True)
class JudgeRequest:
    """One rewrite to check: the two variants."""

    softer: str
    firmer: str
    deadline_seconds: float


@dataclass(frozen=True, slots=True)
class JudgeObservation:
    """The judge's answer: the rude quotes of each variant, plus call metadata."""

    softer_rude: tuple[str, ...]
    firmer_rude: tuple[str, ...]
    meta: GenerationMeta


@dataclass(frozen=True, slots=True)
class JudgeResult:
    """The ``clean`` verdict of each variant, derived by code, plus call metadata."""

    softer_clean: Verdict
    firmer_clean: Verdict
    meta: GenerationMeta


class RewriteJudge(Protocol):
    """Port for the eval judge."""

    async def judge(self, request: JudgeRequest) -> JudgeObservation:
        """Find the rude fragments of ``softer`` and ``firmer`` of one rewrite."""

    def max_billable(self, request: JudgeRequest) -> int:
        """Worst-case billable tokens for ``request`` (input + output cap x attempts)."""
