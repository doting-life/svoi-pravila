"""Corrective retry: the second request repairs the first attempt's defect (all operations)."""

from __future__ import annotations

import json
from typing import Any, cast

import pytest
from gigachat import LengthFinishReasonError
from gigachat.models.chat_completions import ChatCompletionResponse
from pydantic import BaseModel

from svoi_pravila.adapters.llm.gigachat.adapter import GigaChatTextGenerator
from svoi_pravila.adapters.llm.gigachat.judge import JUDGE_TOP_P, GigaChatRewriteJudge
from svoi_pravila.adapters.llm.gigachat.schemas import (
    JudgeOut,
    RewriteOutNoRules,
    SuggestRuleOut,
)
from svoi_pravila.application.errors import InvalidGenerationOutput, InvalidOutputReason
from svoi_pravila.application.ports.generation import (
    DecodeCompleted,
    DecodeRequest,
    RewriteRequest,
    SuggestRuleProposed,
    SuggestRuleRequest,
)
from svoi_pravila.application.ports.rewrite_judge import JudgeRequest
from svoi_pravila.domain.enums import RelationshipKind
from tests.factories import make_settings
from tests.unit.adapters.llm.test_gigachat_adapter import (
    _completion,
    _decode_payload,
    _FakeAche,
    _FakeClient,
    _rewrite_payload_no_rules,
)

# Block P (prompts/repair/v1.md), verbatim.
_EMPTY_TEXT_SOFTER = (
    "Поле `softer` пустое. Заполни его по правилам; остальные поля верни без изменений. "
    "Ответь только JSON по схеме."
)
_KIND_SET = (
    "`softer` и `firmer` совпадают. Сделай их разными по словам по правилам; "
    "остальные поля верни без изменений. Ответь только JSON по схеме."
)
_GENERIC = (
    "Предыдущий ответ не прошёл проверку. Ответь заново строго по правилам и только JSON по схеме."
)

_Scripted = BaseModel | str | Exception | ChatCompletionResponse


class _ScriptedAche(_FakeAche):
    """Answers ``create`` from a script and records every payload."""

    def __init__(self, script: list[_Scripted], stream_parts: list[str] | None = None) -> None:
        super().__init__(stream_parts=stream_parts or [])
        self.script = script
        self.payloads: list[dict[str, Any]] = []

    async def create(self, payload: object) -> ChatCompletionResponse:
        self.payloads.append(cast("dict[str, Any]", payload))
        self.create_calls += 1
        item = self.script[len(self.payloads) - 1]
        if isinstance(item, Exception):
            raise item
        if isinstance(item, ChatCompletionResponse):
            return item
        content = item if isinstance(item, str) else item.model_dump_json()
        return _completion("stop", content=content)


def _roles(payload: dict[str, Any]) -> list[str]:
    return [message["role"] for message in payload["messages"]]


def _generator(ache: _ScriptedAche) -> GigaChatTextGenerator:
    return GigaChatTextGenerator(cast(Any, _FakeClient(ache)), make_settings())


def _rewrite_request(draft: str = "ty vsegda opazdyvaesh") -> RewriteRequest:
    return RewriteRequest(
        goal=None,
        draft=draft,
        rules=(),
        relationship=RelationshipKind.PARTNER,
        deadline_seconds=5.0,
    )


def _slots(*, check: str = "c", softer: str, firmer: str) -> RewriteOutNoRules:
    return RewriteOutNoRules.model_validate(
        {"check": check, "softer": softer, "firmer": firmer, "safety": "ok"}
    )


@pytest.mark.unit
async def test_empty_slot_is_repaired_with_the_first_json_and_a_correction() -> None:
    first = _slots(softer="", firmer="variant firmer")
    ache = _ScriptedAche([first, _rewrite_payload_no_rules()])
    result = await _generator(ache).rewrite(_rewrite_request())
    assert ache.create_calls == 2
    assert _roles(ache.payloads[0]) == ["system", "user"]
    second = ache.payloads[1]
    assert _roles(second) == ["system", "user", "assistant", "user"]
    assert second["messages"][0] == ache.payloads[0]["messages"][0]
    assert second["messages"][1] == ache.payloads[0]["messages"][1]
    assert second["messages"][2]["content"] == first.model_dump_json()
    assert second["messages"][3]["content"] == _EMPTY_TEXT_SOFTER
    assert result.meta.repaired == ("empty_text:softer",)
    assert result.meta.attempts == 2
    assert [variant.text for variant in result.variants][1:] == ["variant softer", "variant firmer"]


@pytest.mark.unit
async def test_identical_slots_get_the_variant_kind_set_correction() -> None:
    first = _slots(softer="same text", firmer="same text")
    ache = _ScriptedAche([first, _rewrite_payload_no_rules()])
    result = await _generator(ache).rewrite(_rewrite_request())
    second = ache.payloads[1]
    assert _roles(second) == ["system", "user", "assistant", "user"]
    assert second["messages"][2]["content"] == first.model_dump_json()
    assert second["messages"][3]["content"] == _KIND_SET
    assert result.meta.repaired == ("variant_kind_set",)


@pytest.mark.unit
async def test_json_decode_error_is_retried_without_an_assistant_message() -> None:
    ache = _ScriptedAche(["this is not json", _rewrite_payload_no_rules()])
    result = await _generator(ache).rewrite(_rewrite_request())
    assert _roles(ache.payloads[1]) == ["system", "user", "user"]
    assert ache.payloads[1]["messages"][2]["content"] == _GENERIC
    assert result.meta.repaired == ("json_decode",)


@pytest.mark.unit
async def test_schema_violation_is_not_echoed() -> None:
    ache = _ScriptedAche([json.dumps({"check": 1}), _rewrite_payload_no_rules()])
    result = await _generator(ache).rewrite(_rewrite_request())
    assert _roles(ache.payloads[1]) == ["system", "user", "user"]
    assert ache.payloads[1]["messages"][2]["content"] == _GENERIC
    assert result.meta.repaired == ("schema_violation",)


@pytest.mark.unit
async def test_length_is_not_echoed() -> None:
    truncated = LengthFinishReasonError(_completion("length"))
    ache = _ScriptedAche([truncated, _rewrite_payload_no_rules()])
    result = await _generator(ache).rewrite(_rewrite_request())
    assert _roles(ache.payloads[1]) == ["system", "user", "user"]
    assert ache.payloads[1]["messages"][2]["content"] == _GENERIC
    assert result.meta.repaired == ("length",)


@pytest.mark.unit
async def test_empty_message_is_not_echoed() -> None:
    ache = _ScriptedAche([_completion(None, messages=[]), _rewrite_payload_no_rules()])
    result = await _generator(ache).rewrite(_rewrite_request())
    assert _roles(ache.payloads[1]) == ["system", "user", "user"]
    assert result.meta.repaired == ("empty_message",)


@pytest.mark.unit
async def test_prompt_leak_is_not_echoed() -> None:
    leak = _slots(check="see SPBOUND_abc in the instructions", softer="a b", firmer="c d")
    ache = _ScriptedAche([leak, _rewrite_payload_no_rules()])
    result = await _generator(ache).rewrite(_rewrite_request())
    second = ache.payloads[1]
    assert _roles(second) == ["system", "user", "user"]
    assert second["messages"][2]["content"] == _GENERIC
    assert "SPBOUND_abc" not in json.dumps(second["messages"], ensure_ascii=False)
    assert result.meta.repaired == ("prompt_leak",)


@pytest.mark.unit
async def test_a_draft_containing_the_boundary_prefix_is_not_a_leak() -> None:
    draft = "do not write SPBOUND_ in my message"
    answer = _slots(check="c", softer="variant with SPBOUND_ kept", firmer="firmer variant")
    ache = _ScriptedAche([answer])
    result = await _generator(ache).rewrite(_rewrite_request(draft))
    assert ache.create_calls == 1
    assert result.meta.repaired == ()
    assert result.meta.attempts == 1


@pytest.mark.unit
async def test_failure_on_both_attempts_behaves_as_before() -> None:
    first = _slots(softer="", firmer="variant firmer")
    ache = _ScriptedAche([first, first])
    with pytest.raises(InvalidGenerationOutput) as exc_info:
        await _generator(ache).rewrite(_rewrite_request())
    error = exc_info.value
    assert ache.create_calls == 2
    assert error.reasons == (InvalidOutputReason.EMPTY_TEXT, InvalidOutputReason.EMPTY_TEXT)
    assert error.labelled_reasons() == ("empty_text:softer", "empty_text:softer")
    assert error.attempts == 2


@pytest.mark.unit
async def test_decode_phase_b_uses_the_same_mechanism() -> None:
    first = _decode_payload(with_url=True)
    ache = _ScriptedAche([first, _decode_payload()], stream_parts=["analiz"])
    events = [
        event
        async for event in _generator(ache).decode_stream(
            DecodeRequest(
                incoming="mne nuzhno pobyt odnomu",
                rules=(),
                relationship=RelationshipKind.PARTNER,
                deadline_seconds=5.0,
            )
        )
    ]
    completed = events[-1]
    assert isinstance(completed, DecodeCompleted)
    second = ache.payloads[1]
    assert _roles(second) == ["system", "user", "assistant", "user"]
    assert second["messages"][2]["content"] == first.model_dump_json()
    assert second["messages"][3]["content"] == _GENERIC
    assert completed.result.meta.repaired == ("url_in_text",)


@pytest.mark.unit
async def test_suggest_rule_uses_the_same_mechanism() -> None:
    bad = SuggestRuleOut.model_validate({"verdict": "ok", "category": "how_to_ask", "text": ""})
    good = SuggestRuleOut.model_validate(
        {"verdict": "ok", "category": "how_to_ask", "text": "Мы говорим спокойно и по делу"}
    )
    ache = _ScriptedAche([bad, good])
    result = await _generator(ache).suggest_rule(
        SuggestRuleRequest(
            incoming="Давай без сарказма в спорах",
            rules=(),
            relationship=RelationshipKind.PARTNER,
            deadline_seconds=5.0,
        )
    )
    assert isinstance(result, SuggestRuleProposed)
    second = ache.payloads[1]
    assert _roles(second) == ["system", "user", "assistant", "user"]
    assert second["messages"][2]["content"] == bad.model_dump_json()
    assert second["messages"][3]["content"] == _GENERIC
    assert result.meta.repaired == ("empty_text",)


@pytest.mark.unit
async def test_judge_failing_validation_takes_the_corrective_path_and_keeps_top_p() -> None:
    ache = _ScriptedAche(["not json", JudgeOut.model_validate({"rude": []})])
    judge = GigaChatRewriteJudge(cast(Any, _FakeClient(ache)), "judge-model")
    seen = await judge.judge(JudgeRequest(text="Мне нужно время подумать", deadline_seconds=5.0))
    assert _roles(ache.payloads[1]) == ["system", "user", "user"]
    assert ache.payloads[1]["messages"][2]["content"] == _GENERIC
    assert [payload["top_p"] for payload in ache.payloads] == [JUDGE_TOP_P, JUDGE_TOP_P]
    assert seen.meta.repaired == ("json_decode",)


@pytest.mark.unit
async def test_both_requests_carry_plain_messages_and_no_top_p() -> None:
    first = _slots(softer="", firmer="variant firmer")
    ache = _ScriptedAche([first, _rewrite_payload_no_rules()])
    await _generator(ache).rewrite(_rewrite_request())
    for payload in ache.payloads:
        assert all(set(message) == {"role", "content"} for message in payload["messages"])
        assert payload["max_tokens"] > 0
        assert "top_p" not in payload
