"""GigaChat clean-judge adapter: prompt, payload, schema, retry and failure accounting."""

from __future__ import annotations

import asyncio
import json
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
from svoi_pravila.adapters.llm.gigachat.schemas import JudgeOut
from svoi_pravila.adapters.llm.registry import load_prompt_template
from svoi_pravila.application.errors import (
    GenerationRefusedByProvider,
    GenerationUnavailable,
    InvalidGenerationOutput,
    InvalidOutputReason,
    UnavailableKind,
)
from svoi_pravila.application.ports.generation import RewriteRequest
from svoi_pravila.application.ports.rewrite_judge import JudgeRequest, RewriteJudge
from svoi_pravila.domain.enums import RelationshipKind
from tests.unit.adapters.llm.test_gigachat_adapter import (
    _collect_decode_stream,
    _decode_payload,
    _FakeAche,
    _FakeClient,
    _generator,
    _rewrite_payload,
    _rule,
)


def _observations(**changes: object) -> dict[str, object]:
    return {
        "softer_rude": changes.get("softer_rude", []),
        "firmer_rude": changes.get("firmer_rude", []),
    }


def _out(**changes: object) -> JudgeOut:
    return JudgeOut.model_validate(_observations(**changes))


def _request(
    *,
    deadline_seconds: float = 5.0,
    softer: str = "Мне нужно время подумать",
    firmer: str = "Мне нужно время",
) -> JudgeRequest:
    return JudgeRequest(softer=softer, firmer=firmer, deadline_seconds=deadline_seconds)


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
async def test_judge_maps_the_quotes_of_each_variant_and_the_meta() -> None:
    out = _out(softer_rude=["блин"], firmer_rude=["ты вечно", "как всегда"])
    seen = await _judge(_FakeAche(create_results=[out])).judge(_request())
    assert seen.softer_rude == ("блин",)
    assert seen.firmer_rude == ("ты вечно", "как всегда")
    assert seen.meta.model == "judge-model"
    assert seen.meta.prompt_version == "judge_clean@v1"
    assert seen.meta.attempts == 1
    assert seen.meta.usage.billable == 8


@pytest.mark.unit
async def test_judge_sends_only_the_low_top_p_and_other_calls_send_none() -> None:
    judged = _RecordingAche(create_results=[_out()])
    await _judge(judged).judge(_request())
    assert judged.payloads[0]["top_p"] == JUDGE_TOP_P == 0.1
    assert judged.payloads[0]["max_tokens"] == MAX_TOKENS_JUDGE == 384

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
    bad = json.dumps({"softer_rude": "блин", "firmer_rude": []})
    ache = _FakeAche(create_results=[bad, _out()])
    seen = await _judge(ache).judge(_request())
    assert ache.create_calls == 2
    assert seen.meta.attempts == 2
    assert seen.meta.usage.billable == 16


@pytest.mark.unit
@pytest.mark.parametrize(
    "bad",
    [
        json.dumps({**_observations(), "extra": "yes"}),
        json.dumps({"softer_rude": []}),
        json.dumps(_observations(softer_rude=["а"] * 5)),
        json.dumps(_observations(firmer_rude=["а"] * 5)),
        json.dumps(_observations(firmer_rude=[""])),
        json.dumps(_observations(softer_rude=["я" * 121])),
        "not json",
    ],
    ids=[
        "extra-field",
        "missing-field",
        "five-softer-quotes",
        "five-firmer-quotes",
        "empty-quote",
        "long-quote",
        "not-json",
    ],
)
async def test_judge_invalid_twice_raises_with_the_reasons(bad: str) -> None:
    ache = _FakeAche(create_results=[bad, bad])
    with pytest.raises(InvalidGenerationOutput) as exc_info:
        await _judge(ache).judge(_request())
    assert len(exc_info.value.reasons) == MAX_ATTEMPTS
    assert exc_info.value.usage.billable == 16
    assert exc_info.value.prompt_version == "judge_clean@v1"


@pytest.mark.unit
def test_the_schema_accepts_the_bounds() -> None:
    quote = "я" * 120
    out = _out(softer_rude=[quote] * 4, firmer_rude=[quote] * 4)
    assert len(out.softer_rude) == len(out.firmer_rude) == 4


@pytest.mark.unit
def test_the_schema_orders_softer_before_firmer() -> None:
    assert list(JudgeOut.model_fields) == ["softer_rude", "firmer_rude"]


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
    assert exc_info.value.prompt_version == "judge_clean@v1"


@pytest.mark.unit
def test_the_prompt_is_block_jc_with_the_boundary_paragraph() -> None:
    template, version = load_prompt_template("judge_clean", "v1")
    assert version == "judge_clean@v1"
    assert template.startswith("Ты — проверяющий в сервисе «Свои правила».")
    assert "Грубое:" in template
    assert "Не грубое:" in template
    assert "`softer:`" in template
    assert "`firmer:`" in template
    assert template.count("{{BOUNDARY_MARKER}}") == 2
    assert "ответ другой модели" in template


@pytest.mark.unit
def test_the_payload_delimits_both_variants_with_one_marker() -> None:
    prepared = _judge(_FakeAche()).prepare(_request(softer="ВАРИАНТ-МЯГЧЕ", firmer="ВАРИАНТ"))
    marker = prepared.boundary_marker
    assert marker.startswith(BOUNDARY_PREFIX)
    lines = prepared.user.splitlines()
    assert lines[0] == f"{marker} payload"
    assert lines[-1] == marker
    assert prepared.user.count(marker) == 2
    assert "softer:\nВАРИАНТ-МЯГЧЕ" in prepared.user
    assert "firmer:\nВАРИАНТ" in prepared.user
    assert "draft:" not in prepared.user
    assert prepared.system.count(marker) == 2
    assert prepared.prompt_version == "judge_clean@v1"


@pytest.mark.unit
def test_a_marker_is_allocated_that_is_not_in_either_variant() -> None:
    clash = f"{BOUNDARY_PREFIX}fixed"
    prepared = _judge(_FakeAche()).prepare(_request(softer=clash, firmer=clash))
    assert prepared.boundary_marker != clash


@pytest.mark.unit
def test_max_billable_counts_both_messages_the_output_cap_and_every_attempt() -> None:
    judge = _judge(_FakeAche())
    prepared = judge.prepare(_request())
    expected = MAX_ATTEMPTS * (
        chars_to_tokens(prepared.system) + chars_to_tokens(prepared.user) + MAX_TOKENS_JUDGE
    )
    assert judge.max_billable(_request()) == expected
