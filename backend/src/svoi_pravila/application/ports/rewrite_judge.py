"""Eval-only port of the clean judge (ADR-0016 §8, §13).

The judge looks for rude fragments in one text per call; it is not told which variant of a rewrite
the text is (``JudgeObservation``). The verdict (``JudgeResult``) is derived from the observation
by code (``evals/judge_verdicts.py``). The quotes of an observation are text: they live in memory
for the local report only. ``JudgeResult`` carries the verdict and call metadata, never text. Only
the eval tools construct a judge.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from svoi_pravila.application.ports.generation import GenerationMeta


class Verdict(StrEnum):
    """The value of ``clean``: ``yes`` when a text has no vulgar or rude language."""

    YES = "yes"
    NO = "no"


@dataclass(frozen=True, slots=True)
class JudgeRequest:
    """One text to check."""

    text: str
    deadline_seconds: float


@dataclass(frozen=True, slots=True)
class JudgeObservation:
    """The judge's answer: the rude quotes of the text, plus call metadata."""

    rude: tuple[str, ...]
    meta: GenerationMeta


@dataclass(frozen=True, slots=True)
class JudgeResult:
    """The ``clean`` verdict of the text, derived by code, plus call metadata."""

    clean: Verdict
    meta: GenerationMeta


class RewriteJudge(Protocol):
    """Port for the eval judge."""

    async def judge(self, request: JudgeRequest) -> JudgeObservation:
        """Find the rude fragments of one text."""

    def max_billable(self, request: JudgeRequest) -> int:
        """Worst-case billable tokens for ``request`` (input + output cap x attempts)."""
