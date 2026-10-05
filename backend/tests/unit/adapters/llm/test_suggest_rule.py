"""Minimal GigaChat suggest_rule adapter validation tests."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, cast

import pytest
from gigachat.models.chat_completions import ChatCompletionResponse

from svoi_pravila.adapters.llm.gigachat.adapter import GigaChatTextGenerator
from svoi_pravila.adapters.llm.gigachat.schemas import SuggestRuleOut, SuggestRuleVerdictOut
from svoi_pravila.adapters.llm.gigachat.validation import (
    MAX_RULE_CHARS,
    to_suggest_rule_result,
)
from svoi_pravila.application.errors import (
    GenerationUnavailable,
    InvalidGenerationOutput,
    InvalidOutputReason,
)
from svoi_pravila.application.ports.generation import (
    GenerationMeta,
    SuggestRuleRequest,
    SuggestRuleVerdict,
    TokenUsage,
)
from svoi_pravila.domain.enums import RelationshipKind, RuleCategory
from tests.factories import make_settings
from tests.unit.adapters.llm.test_gigachat_adapter import _FakeAche, _FakeClient


def _request(*, deadline_seconds: float = 30.0) -> SuggestRuleRequest:
    return SuggestRuleRequest(
        incoming="Давай без сарказма в спорах",
        rules=(),
        relationship=RelationshipKind.PARTNER,
        deadline_seconds=deadline_seconds,
    )


def _gen(ache: _FakeAche) -> GigaChatTextGenerator:
    return GigaChatTextGenerator(cast(Any, _FakeClient(ache)), make_settings())


def _meta() -> GenerationMeta:
    return GenerationMeta(
        model="fake",
        prompt_version="suggest_rule@v1",
        latency_ms=1,
        attempts=1,
        usage=TokenUsage(1, 1, 0),
    )


@pytest.mark.unit
async def test_suggest_rule_ok_and_none() -> None:
    ok_payload = SuggestRuleOut.model_validate(
        {
            "verdict": "ok",
            "category": "how_to_ask",
            "text": "Мы говорим без сарказма в спорах",
        }
    )
    ok = await _gen(_FakeAche(create_results=[ok_payload])).suggest_rule(_request())
    assert ok.verdict is SuggestRuleVerdict.OK
    assert ok.category is RuleCategory.HOW_TO_ASK
    assert ok.text is not None

    none_payload = SuggestRuleOut.model_validate(
        {"verdict": "none", "category": None, "text": None}
    )
    none = await _gen(_FakeAche(create_results=[none_payload])).suggest_rule(_request())
    assert none.verdict is SuggestRuleVerdict.NONE


@pytest.mark.unit
async def test_suggest_rule_invalid_then_retry() -> None:
    bad = SuggestRuleOut.model_validate({"verdict": "ok", "category": "how_to_ask", "text": ""})
    good = SuggestRuleOut.model_validate(
        {
            "verdict": "ok",
            "category": "how_to_ask",
            "text": "Мы говорим спокойно и по делу",
        }
    )
    ache = _FakeAche(create_results=[bad, good])
    result = await _gen(ache).suggest_rule(_request())
    assert result.verdict is SuggestRuleVerdict.OK
    assert ache.create_calls == 2


@pytest.mark.unit
async def test_suggest_rule_rejects_control_chars() -> None:
    bad = SuggestRuleOut.model_validate(
        {"verdict": "ok", "category": "how_to_ask", "text": "плохо\x00текст"}
    )
    ache = _FakeAche(create_results=[bad, bad])
    with pytest.raises(InvalidGenerationOutput):
        await _gen(ache).suggest_rule(_request())


@pytest.mark.unit
async def test_suggest_rule_timeout_unavailable() -> None:
    class SlowAche(_FakeAche):
        async def create(self, payload: object) -> ChatCompletionResponse:
            await asyncio.sleep(10)
            raise RuntimeError("timeout expected")

    with pytest.raises(GenerationUnavailable):
        await _gen(SlowAche()).suggest_rule(_request(deadline_seconds=0.05))


@pytest.mark.unit
def test_to_suggest_rule_result_rejects_schema_and_text_defects() -> None:
    meta = _meta()
    with pytest.raises(InvalidGenerationOutput) as none_extra:
        to_suggest_rule_result(
            SuggestRuleOut.model_validate(
                {"verdict": "none", "category": "how_to_ask", "text": None}
            ),
            meta,
        )
    assert InvalidOutputReason.SCHEMA_VIOLATION in none_extra.value.reasons

    with pytest.raises(InvalidGenerationOutput) as missing:
        to_suggest_rule_result(
            SuggestRuleOut.model_validate({"verdict": "ok", "category": None, "text": "x"}),
            meta,
        )
    assert InvalidOutputReason.SCHEMA_VIOLATION in missing.value.reasons

    with pytest.raises(InvalidGenerationOutput) as long:
        to_suggest_rule_result(
            SuggestRuleOut.model_construct(
                verdict=SuggestRuleVerdictOut.OK,
                category=cast(Any, SimpleNamespace(value="how_to_ask")),
                text="я" * (MAX_RULE_CHARS + 1),
            ),
            meta,
        )
    assert InvalidOutputReason.TEXT_TOO_LONG in long.value.reasons

    with pytest.raises(InvalidGenerationOutput) as url:
        to_suggest_rule_result(
            SuggestRuleOut.model_validate(
                {
                    "verdict": "ok",
                    "category": "how_to_ask",
                    "text": "Смотри https://example.com",
                }
            ),
            meta,
        )
    assert InvalidOutputReason.URL_IN_TEXT in url.value.reasons

    with pytest.raises(InvalidGenerationOutput) as fence:
        to_suggest_rule_result(
            SuggestRuleOut.model_validate(
                {
                    "verdict": "ok",
                    "category": "how_to_ask",
                    "text": "код ```блок``` здесь",
                }
            ),
            meta,
        )
    assert InvalidOutputReason.MARKUP_FENCE in fence.value.reasons

    bogus = SuggestRuleOut.model_construct(
        verdict=SuggestRuleVerdictOut.OK,
        category=cast(Any, SimpleNamespace(value="not_a_real_category")),
        text="Мы говорим спокойно и по делу",
    )
    with pytest.raises(InvalidGenerationOutput) as category:
        to_suggest_rule_result(bogus, meta)
    assert InvalidOutputReason.SCHEMA_VIOLATION in category.value.reasons
