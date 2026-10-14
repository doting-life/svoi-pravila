"""In-memory RewriteJudge for the eval tests: recorded verdicts, no network."""

from __future__ import annotations

from svoi_pravila.application.errors import (
    GenerationRefusedByProvider,
    GenerationUnavailable,
    InvalidGenerationOutput,
)
from svoi_pravila.application.ports.generation import GenerationMeta, TokenUsage
from svoi_pravila.application.ports.rewrite_judge import (
    JudgeRequest,
    JudgeResult,
    SlotVerdicts,
    Verdict,
)

JudgeError = GenerationRefusedByProvider | InvalidGenerationOutput | GenerationUnavailable

JUDGE_USAGE = TokenUsage(input=5, output=6)


def slot(
    *,
    clean: Verdict = Verdict.YES,
    meaning_kept: Verdict = Verdict.YES,
    invented: Verdict = Verdict.NO,
    goal_done: Verdict = Verdict.NA,
    rule_done: Verdict = Verdict.NA,
) -> SlotVerdicts:
    """Verdicts of a good variant unless overridden."""
    return SlotVerdicts(clean, meaning_kept, invented, goal_done, rule_done)


def judge_result(
    softer: SlotVerdicts | None = None,
    firmer: SlotVerdicts | None = None,
    ladder_ok: Verdict = Verdict.YES,
) -> JudgeResult:
    """A judge answer with a fixed usage of 11 billable tokens."""
    return JudgeResult(
        softer=softer or slot(),
        firmer=firmer or slot(),
        ladder_ok=ladder_ok,
        meta=GenerationMeta(
            model="fake-judge",
            prompt_version="judge_rewrite@v1",
            latency_ms=3,
            attempts=1,
            usage=JUDGE_USAGE,
        ),
    )


class FakeRewriteJudge:
    """Answers the scripted outcomes in order; the last one repeats."""

    def __init__(self, *outcomes: JudgeResult | JudgeError) -> None:
        self.outcomes = outcomes or (judge_result(),)
        self.calls: list[JudgeRequest] = []
        self.max_billable_value = 1_000

    def max_billable(self, request: JudgeRequest) -> int:
        """Deterministic worst-case charge."""
        return self.max_billable_value

    async def judge(self, request: JudgeRequest) -> JudgeResult:
        """Record the request and play the next scripted outcome."""
        outcome = self.outcomes[min(len(self.calls), len(self.outcomes) - 1)]
        self.calls.append(request)
        if isinstance(outcome, JudgeResult):
            return outcome
        raise outcome
