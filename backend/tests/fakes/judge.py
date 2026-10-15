"""In-memory RewriteJudge for the eval tests: recorded verdicts, no network."""

from __future__ import annotations

from svoi_pravila.application.errors import (
    GenerationRefusedByProvider,
    GenerationUnavailable,
    InvalidGenerationOutput,
)
from svoi_pravila.application.ports.generation import GenerationMeta, TokenUsage
from svoi_pravila.application.ports.rewrite_judge import (
    GoalBreach,
    JudgeObservation,
    JudgeRequest,
    JudgeResult,
    PairObservation,
    Position,
    RuleCheck,
    SlotObservation,
    SlotVerdicts,
    Verdict,
    Warmer,
)
from svoi_pravila.evals.judge_verdicts import GOAL_BREACHES

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
            prompt_version="judge_rewrite@v3",
            latency_ms=3,
            attempts=1,
            usage=JUDGE_USAGE,
        ),
    )


def _slot_observation(
    request: JudgeRequest, verdicts: SlotVerdicts, variant: str
) -> SlotObservation:
    """An observation that derives to ``verdicts`` for ``request``.

    Every quote is the whole variant: it is in its text, so it counts. A variant that is both
    rude and inventing cannot be scripted (the two quotes would overlap and ``added`` drops).
    """
    if verdicts.clean is Verdict.NO and verdicts.invented is Verdict.YES:
        msg = "a scripted variant cannot be rude and inventing at once"
        raise ValueError(msg)
    breach = GoalBreach.NONE
    if request.goal is not None and verdicts.goal_done is Verdict.NO:
        breach = sorted(GOAL_BREACHES[request.goal])[0]
    return SlotObservation(
        rude=(variant,) if verdicts.clean is Verdict.NO else (),
        added=(variant,) if verdicts.invented is Verdict.YES else (),
        position=Position.SAME if verdicts.meaning_kept is Verdict.YES else Position.LOST,
        goal_sentence="" if request.goal is None else variant,
        goal_breach=breach,
        rule_evidence=(variant,) if verdicts.rule_done is Verdict.YES else (),
        rule_check={
            Verdict.YES: RuleCheck.FOLLOWED,
            Verdict.NO: RuleCheck.BROKEN,
            Verdict.NA: RuleCheck.NOT_APPLICABLE,
        }[verdicts.rule_done],
    )


def observation_of(result: JudgeResult, request: JudgeRequest) -> JudgeObservation:
    """The observation a judge would give for ``request`` so that the verdicts are ``result``.

    A verdict that the request makes impossible (a goal verdict without a goal, a rule verdict
    without rules, a ladder over identical words) cannot be reproduced: the derived verdict
    follows the request.
    """
    return JudgeObservation(
        softer=_slot_observation(request, result.softer, request.softer),
        firmer=_slot_observation(request, result.firmer, request.firmer),
        pair=PairObservation(
            firmer_softening=(),
            firmer_pressure=(),
            warmer=Warmer.SOFTER if result.ladder_ok is Verdict.YES else Warmer.EQUAL,
        ),
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
