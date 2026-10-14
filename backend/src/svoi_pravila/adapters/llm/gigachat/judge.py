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
    JudgeRequest,
    JudgeResult,
    SlotVerdicts,
    Verdict,
)

# The answer is eleven short enum values in a fixed JSON object.
MAX_TOKENS_JUDGE = 384


def _slot_verdicts(parsed: SlotJudgeOut) -> SlotVerdicts:
    return SlotVerdicts(
        clean=Verdict(parsed.clean.value),
        meaning_kept=Verdict(parsed.meaning_kept.value),
        invented=Verdict(parsed.invented.value),
        goal_done=Verdict(parsed.goal_done.value),
        rule_done=Verdict(parsed.rule_done.value),
    )


def _to_result(parsed: JudgeOut, meta: GenerationMeta) -> JudgeResult:
    return JudgeResult(
        softer=_slot_verdicts(parsed.softer),
        firmer=_slot_verdicts(parsed.firmer),
        ladder_ok=Verdict(parsed.ladder_ok.value),
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

    async def judge(self, request: JudgeRequest) -> JudgeResult:
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
                    ),
                    _to_result,
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
