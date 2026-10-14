"""GigaChat rewrite-judge adapter: prompt, payload, schema, retry and failure accounting."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import Any, cast

import pytest
from gigachat.models.chat_completions import ChatCompletionResponse

from svoi_pravila.adapters.llm.gigachat.attempt_policy import MAX_ATTEMPTS
from svoi_pravila.adapters.llm.gigachat.boundaries import BOUNDARY_PREFIX
from svoi_pravila.adapters.llm.gigachat.estimate import chars_to_tokens
from svoi_pravila.adapters.llm.gigachat.judge import MAX_TOKENS_JUDGE, GigaChatRewriteJudge
from svoi_pravila.adapters.llm.gigachat.schemas import JudgeOut
from svoi_pravila.adapters.llm.registry import load_prompt_template
from svoi_pravila.application.errors import (
    GenerationRefusedByProvider,
    GenerationUnavailable,
    InvalidGenerationOutput,
    InvalidOutputReason,
    UnavailableKind,
)
from svoi_pravila.application.ports.generation import RuleContext
from svoi_pravila.application.ports.rewrite_judge import JudgeRequest, RewriteJudge, Verdict
from svoi_pravila.domain.enums import RewriteGoal, RuleCategory
from tests.unit.adapters.llm.test_gigachat_adapter import _FakeAche, _FakeClient

_SLOT = {
    "clean": "yes",
    "meaning_kept": "yes",
    "invented": "no",
    "goal_done": "na",
    "rule_done": "na",
}


def _verdicts(**changes: object) -> dict[str, object]:
    return {
        "softer": {**_SLOT, **cast("dict[str, object]", changes.get("softer", {}))},
        "firmer": {**_SLOT, **cast("dict[str, object]", changes.get("firmer", {}))},
        "ladder_ok": changes.get("ladder_ok", "yes"),
    }


def _out(**changes: object) -> JudgeOut:
    return JudgeOut.model_validate(_verdicts(**changes))


def _request(
    *,
    goal: RewriteGoal | None = None,
    rules: tuple[RuleContext, ...] = (),
    deadline_seconds: float = 5.0,
    softer: str = "Мне нужно время подумать",
    firmer: str = "Мне нужно время",
) -> JudgeRequest:
    return JudgeRequest(
        draft="Дай мне время",
        goal=goal,
        rules=rules,
        softer=softer,
        firmer=firmer,
        deadline_seconds=deadline_seconds,
    )


def _judge(ache: _FakeAche) -> GigaChatRewriteJudge:
    return GigaChatRewriteJudge(cast(Any, _FakeClient(ache)), "judge-model")


@pytest.mark.unit
def test_the_adapter_implements_the_port() -> None:
    judge: RewriteJudge = _judge(_FakeAche())
    assert judge.max_billable(_request()) > 0


@pytest.mark.unit
async def test_judge_maps_every_verdict_and_the_meta() -> None:
    out = _out(
        softer={"clean": "no", "goal_done": "yes", "rule_done": "no"},
        firmer={"meaning_kept": "no", "invented": "yes"},
        ladder_ok="no",
    )
    result = await _judge(_FakeAche(create_results=[out])).judge(_request())
    assert result.softer.clean is Verdict.NO
    assert result.softer.meaning_kept is Verdict.YES
    assert result.softer.invented is Verdict.NO
    assert result.softer.goal_done is Verdict.YES
    assert result.softer.rule_done is Verdict.NO
    assert result.firmer.meaning_kept is Verdict.NO
    assert result.firmer.invented is Verdict.YES
    assert result.firmer.goal_done is Verdict.NA
    assert result.ladder_ok is Verdict.NO
    assert result.meta.model == "judge-model"
    assert result.meta.prompt_version == "judge_rewrite@v1"
    assert result.meta.attempts == 1
    assert result.meta.usage.billable == 8


@pytest.mark.unit
async def test_judge_retries_once_on_a_schema_violation() -> None:
    bad = json.dumps(_verdicts(softer={"clean": "na"}))
    ache = _FakeAche(create_results=[bad, _out()])
    result = await _judge(ache).judge(_request())
    assert ache.create_calls == 2
    assert result.meta.attempts == 2
    assert result.meta.usage.billable == 16


@pytest.mark.unit
@pytest.mark.parametrize(
    "bad",
    [
        json.dumps(_verdicts(ladder_ok="na")),
        json.dumps({**_verdicts(), "extra": "yes"}),
        json.dumps({"softer": _SLOT}),
        "not json",
    ],
    ids=["ladder-na", "extra-field", "missing-slot", "not-json"],
)
async def test_judge_invalid_twice_raises_with_the_reasons(bad: str) -> None:
    ache = _FakeAche(create_results=[bad, bad])
    with pytest.raises(InvalidGenerationOutput) as exc_info:
        await _judge(ache).judge(_request())
    assert len(exc_info.value.reasons) == MAX_ATTEMPTS
    assert exc_info.value.usage.billable == 16
    assert exc_info.value.prompt_version == "judge_rewrite@v1"


@pytest.mark.unit
async def test_judge_provider_refusal_is_reported() -> None:
    with pytest.raises(GenerationRefusedByProvider):
        await _judge(_FakeAche(create_finish="blacklist")).judge(_request())


@pytest.mark.unit
async def test_judge_length_finish_is_an_invalid_output() -> None:
    with pytest.raises(InvalidGenerationOutput) as exc_info:
        await _judge(_FakeAche(create_finish="length")).judge(_request())
    assert set(exc_info.value.reasons) == {InvalidOutputReason.LENGTH}


@pytest.mark.unit
async def test_judge_timeout_is_unavailable_with_the_kind() -> None:
    class SlowAche(_FakeAche):
        async def create(self, payload: object) -> ChatCompletionResponse:
            await asyncio.sleep(10)
            raise RuntimeError("timeout expected")

    with pytest.raises(GenerationUnavailable) as exc_info:
        await _judge(SlowAche()).judge(_request(deadline_seconds=0.05))
    assert exc_info.value.kind is UnavailableKind.TIMEOUT
    assert exc_info.value.prompt_version == "judge_rewrite@v1"


@pytest.mark.unit
def test_the_prompt_is_the_cto_rubric_with_the_boundary_paragraph() -> None:
    template, version = load_prompt_template("judge_rewrite", "v1")
    assert version == "judge_rewrite@v1"
    assert template.startswith("Ты — проверяющий в сервисе «Свои правила».")
    for criterion in ("clean", "meaning_kept", "invented", "goal_done", "rule_done", "ladder_ok"):
        assert f"- `{criterion}` — " in template
    assert "словам." in template
    assert template.count("{{BOUNDARY_MARKER}}") == 2
    assert "ответ другой модели" in template


@pytest.mark.unit
def test_the_payload_delimits_every_untrusted_field_with_one_marker() -> None:
    rule = RuleContext(
        category=RuleCategory.HOW_TO_ASK,
        text="заканчивать вопросом",
        effective_since=datetime(2026, 1, 1, tzinfo=UTC),
    )
    prepared = _judge(_FakeAche()).prepare(
        _request(goal=RewriteGoal.DECLINE, rules=(rule,), softer="ВАРИАНТ-МЯГЧЕ", firmer="ВАРИАНТ")
    )
    marker = prepared.boundary_marker
    assert marker.startswith(BOUNDARY_PREFIX)
    lines = prepared.user.splitlines()
    assert lines[0] == f"{marker} payload"
    assert lines[-1] == marker
    assert prepared.user.count(marker) == 2
    assert "goal:\ndecline" in prepared.user
    assert "category=how_to_ask" in prepared.user
    assert "softer:\nВАРИАНТ-МЯГЧЕ" in prepared.user
    assert "firmer:\nВАРИАНТ" in prepared.user
    assert prepared.system.count(marker) == 2
    assert prepared.prompt_version == "judge_rewrite@v1"


@pytest.mark.unit
def test_a_missing_goal_is_sent_as_none() -> None:
    prepared = _judge(_FakeAche()).prepare(_request())
    assert "goal:\nnone" in prepared.user
    assert "rules:\n(none)" in prepared.user


@pytest.mark.unit
def test_max_billable_counts_both_messages_the_output_cap_and_every_attempt() -> None:
    judge = _judge(_FakeAche())
    prepared = judge.prepare(_request())
    expected = MAX_ATTEMPTS * (
        chars_to_tokens(prepared.system) + chars_to_tokens(prepared.user) + MAX_TOKENS_JUDGE
    )
    assert judge.max_billable(_request()) == expected
