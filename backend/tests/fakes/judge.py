"""In-memory RewriteJudge for the eval tests: recorded verdicts, no network."""

from __future__ import annotations

from svoi_pravila.application.errors import (
    GenerationRefusedByProvider,
    GenerationUnavailable,
    InvalidGenerationOutput,
)
from svoi_pravila.application.ports.generation import GenerationMeta, TokenUsage
from svoi_pravila.application.ports.rewrite_judge import (
    JudgeObservation,
    JudgeRequest,
    JudgeResult,
    Verdict,
)

JudgeError = GenerationRefusedByProvider | InvalidGenerationOutput | GenerationUnavailable

JUDGE_USAGE = TokenUsage(input=5, output=6)


def judge_result(
    softer_clean: Verdict = Verdict.YES, firmer_clean: Verdict = Verdict.YES
) -> JudgeResult:
    """A judge answer with a fixed usage of 11 billable tokens."""
    return JudgeResult(
        softer_clean=softer_clean,
        firmer_clean=firmer_clean,
        meta=GenerationMeta(
            model="fake-judge",
            prompt_version="judge_clean@v1",
            latency_ms=3,
            attempts=1,
            usage=JUDGE_USAGE,
        ),
    )


def observation_of(result: JudgeResult, request: JudgeRequest) -> JudgeObservation:
    """The observation a judge would give for ``request`` so that the verdicts are ``result``.

    A rude variant is quoted whole: the quote is in its text, so it counts.
    """
    return JudgeObservation(
        softer_rude=(request.softer,) if result.softer_clean is Verdict.NO else (),
        firmer_rude=(request.firmer,) if result.firmer_clean is Verdict.NO else (),
        meta=result.meta,
    )


class FakeRewriteJudge:
    """Answers the scripted outcomes in order; the last one repeats.

    A scripted ``JudgeResult`` is turned into an observation that derives to it.
    """

    def __init__(self, *outcomes: JudgeResult | JudgeObservation | JudgeError) -> None:
        self.outcomes = outcomes or (judge_result(),)
        self.calls: list[JudgeRequest] = []
        self.max_billable_value = 1_000

    def max_billable(self, request: JudgeRequest) -> int:
        """Deterministic worst-case charge."""
        return self.max_billable_value

    async def judge(self, request: JudgeRequest) -> JudgeObservation:
        """Record the request and play the next scripted outcome."""
        outcome = self.outcomes[min(len(self.calls), len(self.outcomes) - 1)]
        self.calls.append(request)
        if isinstance(outcome, JudgeResult):
            return observation_of(outcome, request)
        if isinstance(outcome, JudgeObservation):
            return outcome
        raise outcome
