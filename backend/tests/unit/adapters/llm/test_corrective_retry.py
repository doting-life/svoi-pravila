"""Corrective retry: the second request repairs the first attempt's defect (all operations)."""

from __future__ import annotations

import json
from typing import Any, cast

import pytest
from gigachat import LengthFinishReasonError
from gigachat.models.chat_completions import ChatCompletionResponse
from pydantic import BaseModel
from structlog.testing import capture_logs

from svoi_pravila.adapters.llm.gigachat.adapter import GigaChatTextGenerator
from svoi_pravila.adapters.llm.gigachat.judge import JUDGE_TOP_P, GigaChatRewriteJudge
from svoi_pravila.adapters.llm.gigachat.schemas import (
    DecodeOut,
    JudgeOut,
    RewriteOutNoRules,
    SuggestRuleOut,
)
from svoi_pravila.adapters.llm.gigachat.structured import LabelledInvalidAttempt
from svoi_pravila.adapters.system.noop_generation_attempt_observer import (
    NoopGenerationAttemptObserver,
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
from svoi_pravila.domain.enums import RelationshipKind, VariantKind
from tests.factories import make_settings
from tests.fakes.generation_attempt_observer import RecordingAttemptObserver
from tests.unit.adapters.llm.test_gigachat_adapter import (
    _completion,
    _decode_payload,
    _FakeAche,
    _FakeClient,
    _rewrite_payload_no_rules,
)

# Block R3 (prompts/repair/v3.md), verbatim: one line per defect, then ``keep`` once.
_KEEP = "Остальные поля верни без изменений. Ответь только JSON по схеме."
_EMPTY_TEXT_SOFTER_LINE = (
    "Поле `softer` пустое. Заполни его по правилам; если после перевода грубых слов от фразы "
    "ничего не осталось, передай намерение автора спокойными словами."
)
_EMPTY_TEXT_FIRMER_LINE = _EMPTY_TEXT_SOFTER_LINE.replace("`softer`", "`firmer`")
_TOO_LONG_FIRMER_LINE = "Поле `firmer` слишком длинное. Сократи его по правилам."
_EMPTY_TEXT_SOFTER = f"{_EMPTY_TEXT_SOFTER_LINE}\n{_KEEP}"
_KIND_SET = f"`softer` и `firmer` совпадают. Сделай их разными по словам по правилам.\n{_KEEP}"
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
    return GigaChatTextGenerator(
        cast(Any, _FakeClient(ache)), make_settings(), NoopGenerationAttemptObserver()
    )


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
async def test_both_slots_empty_get_one_correction_with_two_lines_and_keep_once() -> None:
    first = _slots(softer="", firmer="")
    ache = _ScriptedAche([first, _clean_slots()])
    result = await _generator(ache).rewrite(_rewrite_request())
    correction = ache.payloads[1]["messages"][3]["content"]
    assert correction == f"{_EMPTY_TEXT_SOFTER_LINE}\n{_EMPTY_TEXT_FIRMER_LINE}\n{_KEEP}"
    assert correction.count(_KEEP) == 1
    assert ache.create_calls == 2
    assert result.meta.repaired == ("empty_text:softer", "empty_text:firmer")


@pytest.mark.unit
async def test_one_empty_slot_gets_one_line_and_keep() -> None:
    first = _slots(softer="давай обсудим спокойно", firmer="")
    ache = _ScriptedAche([first, _clean_slots()])
    result = await _generator(ache).rewrite(_rewrite_request())
    assert ache.payloads[1]["messages"][3]["content"] == f"{_EMPTY_TEXT_FIRMER_LINE}\n{_KEEP}"
    assert result.meta.repaired == ("empty_text:firmer",)


@pytest.mark.unit
async def test_both_slots_empty_on_both_attempts_still_report_one_reason_per_attempt() -> None:
    first = _slots(softer="", firmer="")
    ache = _ScriptedAche([first, first])
    with pytest.raises(InvalidGenerationOutput) as exc_info:
        await _generator(ache).rewrite(_rewrite_request())
    error = exc_info.value
    assert error.reasons == (InvalidOutputReason.EMPTY_TEXT, InvalidOutputReason.EMPTY_TEXT)
    assert error.slots == (VariantKind.SOFTER, VariantKind.SOFTER)
    assert error.labelled_reasons() == ("empty_text:softer", "empty_text:softer")


@pytest.mark.unit
async def test_a_defect_without_its_own_line_gets_generic_alone_even_next_to_an_empty_slot() -> (
    None
):
    first = _slots(check="see https://example.test/x", softer="", firmer="мне это важно")
    ache = _ScriptedAche([first, _clean_slots()])
    result = await _generator(ache).rewrite(_rewrite_request())
    correction = ache.payloads[1]["messages"][3]["content"]
    assert correction == _GENERIC
    assert _KEEP not in correction
    assert result.meta.repaired == ("url_in_text:check", "empty_text:softer")


@pytest.mark.unit
async def test_the_observer_gets_every_defect_label_of_an_attempt() -> None:
    first = _slots(softer="", firmer="")
    ache = _ScriptedAche([first, first])
    generator, observer = _observed(ache)
    with pytest.raises(InvalidGenerationOutput):
        await generator.rewrite(_rewrite_request())
    assert [
        (item.reason, item.slot, item.labels)
        for item in cast("list[LabelledInvalidAttempt]", observer.attempts)
    ] == [
        (
            InvalidOutputReason.EMPTY_TEXT,
            VariantKind.SOFTER,
            ("empty_text:softer", "empty_text:firmer"),
        )
    ] * 2


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


_PROFANITY_FIRMER_LINE = (
    "В поле `firmer` остались грубые слова: сука. Переведи их на нормативный язык по смыслу: "
    "усилитель убери, оскорбление замени названием поступка, грубое требование скажи теми же "
    "словами без грубости."
)
_GENERALIZATION_SOFTER_LINE = (
    "В поле `softer` есть обобщение: ты всегда. Скажи о конкретном случае, без обобщения."
)
_PROFANITY_SOFTER_LINE = _PROFANITY_FIRMER_LINE.replace("`firmer`", "`softer`")
_PROFANITY_FIRMER = f"{_PROFANITY_FIRMER_LINE}\n{_KEEP}"
_GENERALIZATION_SOFTER = f"{_GENERALIZATION_SOFTER_LINE}\n{_KEEP}"
_CLEAN = {"check": "c", "softer": "давай обсудим спокойно", "firmer": "мне это важно"}


def _observed(
    ache: _ScriptedAche,
) -> tuple[GigaChatTextGenerator, RecordingAttemptObserver]:
    observer = RecordingAttemptObserver()
    generator = GigaChatTextGenerator(cast(Any, _FakeClient(ache)), make_settings(), observer)
    return generator, observer


def _clean_slots() -> RewriteOutNoRules:
    return _slots(**_CLEAN)


@pytest.mark.unit
async def test_profanity_in_firmer_is_repaired_with_the_echo_and_a_correction() -> None:
    first = _slots(softer="давай обсудим спокойно", firmer="ты сука")
    ache = _ScriptedAche([first, _clean_slots()])
    result = await _generator(ache).rewrite(_rewrite_request())
    second = ache.payloads[1]
    assert _roles(second) == ["system", "user", "assistant", "user"]
    assert second["messages"][2]["content"] == first.model_dump_json()
    assert second["messages"][3]["content"] == _PROFANITY_FIRMER
    assert result.meta.repaired == ("profanity:firmer",)
    assert result.meta.residual == ()
    assert result.meta.attempts == 2
    assert [variant.text for variant in result.variants][1:] == [
        _CLEAN["softer"],
        _CLEAN["firmer"],
    ]


@pytest.mark.unit
async def test_profanity_left_after_the_retry_fails_the_call_with_the_slot() -> None:
    first = _slots(softer="давай обсудим спокойно", firmer="ты сука")
    ache = _ScriptedAche([first, first])
    with pytest.raises(InvalidGenerationOutput) as exc_info:
        await _generator(ache).rewrite(_rewrite_request())
    error = exc_info.value
    assert ache.create_calls == 2
    assert error.reasons == (InvalidOutputReason.PROFANITY, InvalidOutputReason.PROFANITY)
    assert error.labelled_reasons() == ("profanity:firmer", "profanity:firmer")
    assert error.attempts == 2
    assert "сука" not in repr(error)
    assert "сука" not in str(error)


@pytest.mark.unit
async def test_a_generalization_left_after_the_retry_is_accepted_and_reported() -> None:
    first = _slots(softer="ты всегда опаздываешь", firmer="мне это важно")
    ache = _ScriptedAche([first, first])
    result = await _generator(ache).rewrite(_rewrite_request())
    assert ache.create_calls == 2
    second = ache.payloads[1]
    assert second["messages"][3]["content"] == _GENERALIZATION_SOFTER
    assert result.meta.repaired == ("generalization:softer",)
    assert result.meta.residual == (VariantKind.SOFTER,)
    assert [variant.text for variant in result.variants][1] == "ты всегда опаздываешь"


@pytest.mark.unit
async def test_a_repaired_generalization_leaves_no_residual() -> None:
    first = _slots(softer="ты всегда опаздываешь", firmer="мне это важно")
    ache = _ScriptedAche([first, _clean_slots()])
    result = await _generator(ache).rewrite(_rewrite_request())
    assert result.meta.repaired == ("generalization:softer",)
    assert result.meta.residual == ()


@pytest.mark.unit
async def test_profanity_in_check_only_is_not_a_finding() -> None:
    answer = _slots(check="ты сука, ты всегда так", softer="давай обсудим", firmer="мне это важно")
    ache = _ScriptedAche([answer])
    result = await _generator(ache).rewrite(_rewrite_request())
    assert ache.create_calls == 1
    assert result.meta.repaired == ()
    assert result.meta.residual == ()


@pytest.mark.unit
async def test_findings_in_both_slots_get_one_correction_with_two_lines() -> None:
    first = _slots(softer="ты всегда опаздываешь", firmer="ты сука")
    ache = _ScriptedAche([first, _clean_slots()])
    result = await _generator(ache).rewrite(_rewrite_request())
    second = ache.payloads[1]
    assert _roles(second) == ["system", "user", "assistant", "user"]
    assert second["messages"][3]["content"] == "\n".join(
        [_GENERALIZATION_SOFTER_LINE, _PROFANITY_FIRMER_LINE, _KEEP]
    )
    assert result.meta.repaired == ("generalization:softer", "profanity:firmer")


@pytest.mark.unit
async def test_profanity_has_priority_over_a_generalization_in_the_failure() -> None:
    first = _slots(softer="ты сука, ты всегда так", firmer="мне это важно")
    ache = _ScriptedAche([first, first])
    with pytest.raises(InvalidGenerationOutput) as exc_info:
        await _generator(ache).rewrite(_rewrite_request())
    assert exc_info.value.labelled_reasons() == ("profanity:softer", "profanity:softer")
    assert ache.payloads[1]["messages"][3]["content"] == "\n".join(
        [_PROFANITY_SOFTER_LINE, _GENERALIZATION_SOFTER_LINE, _KEEP]
    )


@pytest.mark.unit
async def test_a_formal_defect_has_priority_over_a_lexicon_finding() -> None:
    first = _slots(softer="", firmer="ты сука")
    ache = _ScriptedAche([first, _clean_slots()])
    result = await _generator(ache).rewrite(_rewrite_request())
    assert ache.payloads[1]["messages"][3]["content"] == _EMPTY_TEXT_SOFTER
    assert result.meta.repaired == ("empty_text:softer",)


@pytest.mark.unit
async def test_a_second_attempt_after_a_formal_defect_is_checked_with_the_final_rules() -> None:
    first = _slots(softer="", firmer="мне это важно")
    ache = _ScriptedAche([first, _slots(softer="давай обсудим", firmer="ты сука")])
    with pytest.raises(InvalidGenerationOutput) as exc_info:
        await _generator(ache).rewrite(_rewrite_request())
    assert exc_info.value.labelled_reasons() == ("empty_text:softer", "profanity:firmer")


@pytest.mark.unit
async def test_a_non_ok_safety_answer_is_not_checked() -> None:
    answer = RewriteOutNoRules.model_validate(
        {"check": "", "softer": "ты сука", "firmer": "ты сука", "safety": "crisis"}
    )
    ache = _ScriptedAche([answer])
    result = await _generator(ache).rewrite(_rewrite_request())
    assert ache.create_calls == 1
    assert result.variants == ()


@pytest.mark.unit
async def test_decode_phase_b_uses_the_same_lexicon_path() -> None:
    first = DecodeOut.model_validate(
        {
            "hypotheses": ["h1"],
            "underlying_request": "needs space",
            "variants": [
                {"text": "softer-reply", "kind": "softer"},
                {"text": "ты сука", "kind": "firmer"},
            ],
            "applied_rule_indexes": [],
            "safety": "ok",
        }
    )
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
    assert second["messages"][2]["content"] == first.model_dump_json()
    assert second["messages"][3]["content"] == _PROFANITY_FIRMER
    assert completed.result.meta.repaired == ("profanity:firmer",)
    assert completed.result.meta.residual == ()


@pytest.mark.unit
async def test_the_observer_gets_every_invalid_attempt_with_the_answer_fields() -> None:
    first = _slots(softer="давай обсудим спокойно", firmer="ты сука")
    ache = _ScriptedAche([first, first])
    generator, observer = _observed(ache)
    with pytest.raises(InvalidGenerationOutput):
        await generator.rewrite(_rewrite_request())
    assert [
        (item.operation, item.attempt, item.reason, item.slot) for item in observer.attempts
    ] == [
        ("rewrite", 0, InvalidOutputReason.PROFANITY, VariantKind.FIRMER),
        ("rewrite", 1, InvalidOutputReason.PROFANITY, VariantKind.FIRMER),
    ]
    assert observer.attempts[0].texts == (
        ("check", "c"),
        ("firmer", "ты сука"),
        ("softer", "давай обсудим спокойно"),
        ("safety", "ok"),
    )
    assert "сука" not in repr(observer.attempts[0])


@pytest.mark.unit
async def test_the_observer_gets_the_raw_text_of_an_unparsed_answer() -> None:
    ache = _ScriptedAche(["this is not json", _clean_slots()])
    generator, observer = _observed(ache)
    await generator.rewrite(_rewrite_request())
    [attempt] = observer.attempts
    assert attempt.reason is InvalidOutputReason.JSON_DECODE
    assert attempt.slot is None
    assert attempt.texts == (("raw", "this is not json"),)


@pytest.mark.unit
async def test_the_observer_gets_no_text_when_the_answer_had_none() -> None:
    ache = _ScriptedAche([_completion(None, messages=[]), _clean_slots()])
    generator, observer = _observed(ache)
    await generator.rewrite(_rewrite_request())
    [attempt] = observer.attempts
    assert attempt.reason is InvalidOutputReason.EMPTY_MESSAGE
    assert attempt.texts == ()


@pytest.mark.unit
async def test_the_observer_names_nested_fields_of_a_decode_answer() -> None:
    first = DecodeOut.model_validate(
        {
            "hypotheses": ["h1"],
            "underlying_request": "needs space",
            "variants": [{"text": "https://example.test/x", "kind": "softer"}],
            "applied_rule_indexes": [3],
            "safety": "ok",
        }
    )
    ache = _ScriptedAche([first, _decode_payload()], stream_parts=["analiz"])
    observer = RecordingAttemptObserver()
    generator = GigaChatTextGenerator(cast(Any, _FakeClient(ache)), make_settings(), observer)
    [
        event
        async for event in generator.decode_stream(
            DecodeRequest(
                incoming="mne nuzhno pobyt odnomu",
                rules=(),
                relationship=RelationshipKind.PARTNER,
                deadline_seconds=5.0,
            )
        )
    ]
    assert dict(observer.attempts[0].texts) == {
        "hypotheses[0]": "h1",
        "underlying_request": "needs space",
        "variants[0].text": "https://example.test/x",
        "variants[0].kind": "softer",
        "safety": "ok",
    }


@pytest.mark.unit
async def test_matched_words_never_reach_the_logs_of_the_retry_path() -> None:
    first = _slots(softer="давай обсудим спокойно", firmer="ты сука, ты всегда так")
    ache = _ScriptedAche([first, first])
    with capture_logs() as logs, pytest.raises(InvalidGenerationOutput):
        await _generator(ache).rewrite(_rewrite_request())
    assert logs
    rendered = json.dumps(logs, ensure_ascii=False, default=str)
    assert "сука" not in rendered
    assert "всегда" not in rendered
