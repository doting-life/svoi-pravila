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
from svoi_pravila.adapters.llm.gigachat.judge import (
    JUDGE_TOP_P,
    MAX_TOKENS_JUDGE,
    GigaChatRewriteJudge,
)
from svoi_pravila.adapters.llm.gigachat.schemas import JudgeOut, SlotJudgeOut
from svoi_pravila.adapters.llm.registry import load_prompt_template
from svoi_pravila.application.errors import (
    GenerationRefusedByProvider,
    GenerationUnavailable,
    InvalidGenerationOutput,
    InvalidOutputReason,
    UnavailableKind,
)
from svoi_pravila.application.ports.generation import RewriteRequest, RuleContext
from svoi_pravila.application.ports.rewrite_judge import (
    GoalBreach,
    JudgeRequest,
    PairObservation,
    Position,
    RewriteJudge,
    RuleCheck,
    SlotObservation,
    Warmer,
)
from svoi_pravila.domain.enums import RelationshipKind, RewriteGoal, RuleCategory
from tests.unit.adapters.llm.test_gigachat_adapter import (
    _collect_decode_stream,
    _decode_payload,
    _FakeAche,
    _FakeClient,
    _generator,
    _rewrite_payload,
    _rule,
)

_SLOT: dict[str, object] = {
    "rude": [],
    "added": [],
    "position": "same",
    "goal_sentence": "",
    "goal_breach": "none",
    "rule_evidence": [],
    "rule_check": "not_applicable",
}


def _observations(**changes: object) -> dict[str, object]:
    return {
        "softer": {**_SLOT, **cast("dict[str, object]", changes.get("softer", {}))},
        "firmer": {**_SLOT, **cast("dict[str, object]", changes.get("firmer", {}))},
        "firmer_softening": changes.get("firmer_softening", []),
        "firmer_pressure": changes.get("firmer_pressure", []),
        "warmer": changes.get("warmer", "softer"),
    }


def _out(**changes: object) -> JudgeOut:
    return JudgeOut.model_validate(_observations(**changes))


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


class _RecordingAche(_FakeAche):
    def __init__(self, create_results: list[Any], stream_parts: list[str] | None = None) -> None:
        super().__init__(create_results=create_results, stream_parts=stream_parts or [])
        self.payloads: list[dict[str, Any]] = []

    async def create(self, payload: object) -> ChatCompletionResponse:
        self.payloads.append(cast("dict[str, Any]", payload))
        return await super().create(payload)


@pytest.mark.unit
async def test_judge_maps_every_observation_and_the_meta() -> None:
    out = _out(
        softer={
            "rude": ["блин"],
            "goal_sentence": "Я не пойду.",
            "goal_breach": "hedged",
            "rule_evidence": ["Заканчиваю просьбой"],
            "rule_check": "broken",
        },
        firmer={"added": ["иначе уйду"], "position": "weakened"},
        firmer_softening=["если можно"],
        firmer_pressure=["или уйду"],
        warmer="equal",
    )
    seen = await _judge(_FakeAche(create_results=[out])).judge(_request())
    assert seen.softer == SlotObservation(
        rude=("блин",),
        added=(),
        position=Position.SAME,
        goal_sentence="Я не пойду.",
        goal_breach=GoalBreach.HEDGED,
        rule_evidence=("Заканчиваю просьбой",),
        rule_check=RuleCheck.BROKEN,
    )
    assert seen.firmer.added == ("иначе уйду",)
    assert seen.firmer.position is Position.WEAKENED
    assert seen.pair == PairObservation(
        firmer_softening=("если можно",),
        firmer_pressure=("или уйду",),
        warmer=Warmer.EQUAL,
    )
    assert seen.meta.model == "judge-model"
    assert seen.meta.prompt_version == "judge_rewrite@v3"
    assert seen.meta.attempts == 1
    assert seen.meta.usage.billable == 8


@pytest.mark.unit
async def test_judge_sends_only_the_low_top_p_and_other_calls_send_none() -> None:
    judged = _RecordingAche(create_results=[_out()])
    await _judge(judged).judge(_request())
    assert judged.payloads[0]["top_p"] == JUDGE_TOP_P == 0.1
    assert judged.payloads[0]["max_tokens"] == MAX_TOKENS_JUDGE == 1024

    rewritten = _RecordingAche(create_results=[_rewrite_payload()])
    await _generator(rewritten).rewrite(
        RewriteRequest(
            goal=None,
            draft="ty vsegda opazdyvaesh",
            rules=(_rule(),),
            relationship=RelationshipKind.PARTNER,
            deadline_seconds=5.0,
        )
    )
    decoded = _RecordingAche(create_results=[_decode_payload()], stream_parts=["analiz"])
    await _collect_decode_stream(decoded)
    for ache in (rewritten, decoded):
        assert set(ache.payloads[0]) == {"model", "messages", "max_tokens", "response_format"}


@pytest.mark.unit
async def test_judge_retries_once_on_a_schema_violation() -> None:
    bad = json.dumps(_observations(softer={"position": "na"}))
    ache = _FakeAche(create_results=[bad, _out()])
    seen = await _judge(ache).judge(_request())
    assert ache.create_calls == 2
    assert seen.meta.attempts == 2
    assert seen.meta.usage.billable == 16


@pytest.mark.unit
@pytest.mark.parametrize(
    "bad",
    [
        json.dumps(_observations(warmer="na")),
        json.dumps({**_observations(), "extra": "yes"}),
        json.dumps({"softer": _SLOT}),
        json.dumps(_observations(softer={"rude": ["а"] * 5})),
        json.dumps(_observations(firmer={"rule_evidence": ["а"] * 5})),
        json.dumps(_observations(firmer={"rule_evidence": [""]})),
        json.dumps(_observations(firmer_pressure=[""])),
        json.dumps(_observations(firmer_softening=["я" * 121])),
        json.dumps(_observations(firmer={"goal_sentence": "я" * 301})),
        json.dumps(_observations(softer={"goal_breach": "shouting"})),
        "not json",
    ],
    ids=[
        "warmer-na",
        "extra-field",
        "missing-slot",
        "five-quotes",
        "five-rule-quotes",
        "empty-rule-quote",
        "empty-quote",
        "long-quote",
        "long-goal-sentence",
        "unknown-breach",
        "not-json",
    ],
)
async def test_judge_invalid_twice_raises_with_the_reasons(bad: str) -> None:
    ache = _FakeAche(create_results=[bad, bad])
    with pytest.raises(InvalidGenerationOutput) as exc_info:
        await _judge(ache).judge(_request())
    assert len(exc_info.value.reasons) == MAX_ATTEMPTS
    assert exc_info.value.usage.billable == 16
    assert exc_info.value.prompt_version == "judge_rewrite@v3"


@pytest.mark.unit
def test_the_schema_accepts_the_bounds() -> None:
    quote = "я" * 120
    out = _out(
        softer={"rude": [quote] * 4, "goal_sentence": "я" * 300, "rule_evidence": [quote] * 4},
        firmer_softening=[quote] * 4,
    )
    assert len(out.softer.rude) == 4
    assert len(out.softer.goal_sentence) == 300


@pytest.mark.unit
def test_the_schema_orders_slots_then_pair_fields_and_evidence_before_enums() -> None:
    assert list(JudgeOut.model_fields) == [
        "softer",
        "firmer",
        "firmer_softening",
        "firmer_pressure",
        "warmer",
    ]
    assert list(SlotJudgeOut.model_fields) == [
        "rude",
        "added",
        "position",
        "goal_sentence",
        "goal_breach",
        "rule_evidence",
        "rule_check",
    ]


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
    assert exc_info.value.prompt_version == "judge_rewrite@v3"


@pytest.mark.unit
def test_the_prompt_is_the_cto_rubric_with_the_boundary_paragraph() -> None:
    template, version = load_prompt_template("judge_rewrite", "v3")
    assert version == "judge_rewrite@v3"
    assert template.startswith("Ты — проверяющий в сервисе «Свои правила».")
    assert "Ты не ставишь оценок" in template
    for field in (*SlotJudgeOut.model_fields, *JudgeOut.model_fields):
        if field not in {"softer", "firmer"}:
            assert f"- `{field}` — " in template
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
    assert prepared.prompt_version == "judge_rewrite@v3"


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
