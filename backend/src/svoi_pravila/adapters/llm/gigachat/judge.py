"""GigaChat implementation of the eval-only RewriteJudge port.

Only the eval tools construct this class; ``bootstrap`` and ``api`` must not import it
(import contract in ``pyproject.toml``).
"""

from __future__ import annotations

import asyncio
import time

from gigachat import GigaChat

from svoi_pravila.adapters.llm.gigachat.attempt_policy import (
    AttemptState,
    token_usage_from_state,
)
from svoi_pravila.adapters.llm.gigachat.estimate import max_structured_billable
from svoi_pravila.adapters.llm.gigachat.prepared import PreparedMessages, prepare_judge
from svoi_pravila.adapters.llm.gigachat.schemas import JudgeOut
from svoi_pravila.adapters.llm.gigachat.structured import StructuredCallParams, structured_call
from svoi_pravila.application.errors import GenerationUnavailable, UnavailableKind
from svoi_pravila.application.ports.generation import GenerationMeta
from svoi_pravila.application.ports.rewrite_judge import JudgeObservation, JudgeRequest

# Up to four quotes of one text.
MAX_TOKENS_JUDGE = 384
# Sampling of judge calls only: low, to make the observations repeatable.
JUDGE_TOP_P = 0.1


def _to_observation(parsed: JudgeOut, meta: GenerationMeta) -> JudgeObservation:
    return JudgeObservation(
        rude=tuple(parsed.rude),
        meta=meta,
    )


def max_judge_billable(request: JudgeRequest) -> int:
    """Worst-case billable tokens of one judge call, including its corrective retry."""
    return max_structured_billable(prepare_judge(request), output_cap=MAX_TOKENS_JUDGE)


class GigaChatRewriteJudge:
    """RewriteJudge backed by the official GigaChat async SDK."""

    def __init__(self, client: GigaChat, model: str) -> None:
        self._client = client
        self._model = model

    def prepare(self, request: JudgeRequest) -> PreparedMessages:
        """Build rendered system/user messages (also used by tests)."""
        return prepare_judge(request)

    def max_billable(self, request: JudgeRequest) -> int:
        """Worst-case billable tokens: both attempts, the second with the correction."""
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
                        operation="judge_clean",
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
