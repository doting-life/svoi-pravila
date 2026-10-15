"""GigaChat implementation of the eval-only RewriteJudge port.

Only the eval tools construct this class; ``bootstrap`` and ``api`` must not import it
(import contract in ``pyproject.toml``).
"""

from __future__ import annotations

import asyncio
import time

from gigachat import GigaChat

from svoi_pravila.adapters.llm.gigachat.attempt_policy import (
    MAX_ATTEMPTS,
    AttemptState,
    token_usage_from_state,
)
from svoi_pravila.adapters.llm.gigachat.estimate import estimate_prepared_tokens
from svoi_pravila.adapters.llm.gigachat.prepared import PreparedMessages, prepare_judge
from svoi_pravila.adapters.llm.gigachat.schemas import JudgeOut, SlotJudgeOut
from svoi_pravila.adapters.llm.gigachat.structured import StructuredCallParams, structured_call
from svoi_pravila.application.errors import GenerationUnavailable, UnavailableKind
from svoi_pravila.application.ports.generation import GenerationMeta
from svoi_pravila.application.ports.rewrite_judge import (
    GoalBreach,
    JudgeObservation,
    JudgeRequest,
    PairObservation,
    Position,
    RuleCheck,
    SlotObservation,
    Warmer,
)

# Quotes of up to four items per list, two variants and the pair, plus the goal sentences.
MAX_TOKENS_JUDGE = 1024
# Sampling of judge calls only: low, to make the observations repeatable.
JUDGE_TOP_P = 0.1


def _slot_observation(parsed: SlotJudgeOut) -> SlotObservation:
    return SlotObservation(
        rude=tuple(parsed.rude),
        added=tuple(parsed.added),
        goal_sentence=parsed.goal_sentence,
        position=Position(parsed.position.value),
        goal_breach=GoalBreach(parsed.goal_breach.value),
        rule_check=RuleCheck(parsed.rule_check.value),
    )


def _to_observation(parsed: JudgeOut, meta: GenerationMeta) -> JudgeObservation:
    return JudgeObservation(
        softer=_slot_observation(parsed.softer),
        firmer=_slot_observation(parsed.firmer),
        pair=PairObservation(
            firmer_softening=tuple(parsed.firmer_softening),
            firmer_pressure=tuple(parsed.firmer_pressure),
            warmer=Warmer(parsed.warmer.value),
        ),
        meta=meta,
    )


def max_judge_billable(request: JudgeRequest) -> int:
    """Worst-case billable tokens of one judge call: rendered messages plus the output cap."""
    prepared = prepare_judge(request)
    one = estimate_prepared_tokens(prepared.system, prepared.user, output_cap=MAX_TOKENS_JUDGE)
    return MAX_ATTEMPTS * one


class GigaChatRewriteJudge:
    """RewriteJudge backed by the official GigaChat async SDK."""

    def __init__(self, client: GigaChat, model: str) -> None:
        self._client = client
        self._model = model

    def prepare(self, request: JudgeRequest) -> PreparedMessages:
        """Build rendered system/user messages (also used by tests)."""
        return prepare_judge(request)

    def max_billable(self, request: JudgeRequest) -> int:
        """Worst-case billable tokens: rendered messages plus the output cap, per attempt."""
        return max_judge_billable(request)

    async def judge(self, request: JudgeRequest) -> JudgeObservation:
        prepared = self.prepare(request)
        state = AttemptState(
            started=time.perf_counter(),
            model=self._model,
            prompt_version=prepared.prompt_version,
        )
        deadline = asyncio.get_running_loop().time() + request.deadline_seconds
        try:
            async with asyncio.timeout_at(deadline):
                return await structured_call(
                    StructuredCallParams(
                        client=self._client,
                        operation="judge_rewrite",
                        model=self._model,
                        prepared=prepared,
                        response_format=JudgeOut,
                        max_tokens=MAX_TOKENS_JUDGE,
                        top_p=JUDGE_TOP_P,
                    ),
                    _to_observation,
                    state=state,
                )
        except TimeoutError:
            raise GenerationUnavailable(
                UnavailableKind.TIMEOUT,
                usage=token_usage_from_state(state),
                attempts=state.attempts,
                model=state.model,
                prompt_version=state.prompt_version,
            ) from None
