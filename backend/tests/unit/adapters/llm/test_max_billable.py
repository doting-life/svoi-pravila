"""Known-prompt max_billable values for each generation operation."""

from __future__ import annotations

import pytest

from svoi_pravila.adapters.llm.gigachat.estimate import (
    estimate_prepared_tokens,
    max_billable_for_request,
    max_structured_billable,
)
from svoi_pravila.adapters.llm.gigachat.prepared import (
    prepare_decode,
    prepare_decode_analysis,
    prepare_rewrite,
    prepare_suggest_rule,
)
from svoi_pravila.adapters.llm.gigachat.validation import (
    MAX_ANALYSIS_CHARS,
    MAX_TOKENS_ANALYSIS,
    MAX_TOKENS_DECODE,
    MAX_TOKENS_REWRITE,
    MAX_TOKENS_SUGGEST,
)
from svoi_pravila.application.ports.generation import (
    DecodeRequest,
    RewriteRequest,
    SuggestRuleRequest,
)
from svoi_pravila.domain.enums import RelationshipKind, RewriteGoal


@pytest.mark.unit
@pytest.mark.parametrize("goal", [None, RewriteGoal.DECLINE])
def test_max_billable_rewrite_known_prompt(goal: RewriteGoal | None) -> None:
    request = RewriteRequest(
        draft="пожалуйста говори спокойнее",
        goal=goal,
        rules=(),
        relationship=RelationshipKind.PARTNER,
        deadline_seconds=5.0,
    )
    prepared = prepare_rewrite(request)
    expected = max_structured_billable(prepared, output_cap=MAX_TOKENS_REWRITE)
    assert max_billable_for_request(request) == expected
    one = estimate_prepared_tokens(prepared.system, prepared.user, output_cap=MAX_TOKENS_REWRITE)
    assert expected > 2 * one


@pytest.mark.unit
def test_max_billable_decode_known_prompt() -> None:
    request = DecodeRequest(
        incoming="что ты этим хотел сказать",
        rules=(),
        relationship=RelationshipKind.FAMILY,
        deadline_seconds=45.0,
    )
    analysis = prepare_decode_analysis(request)
    phase_a = estimate_prepared_tokens(
        analysis.system, analysis.user, output_cap=MAX_TOKENS_ANALYSIS
    )
    structured = prepare_decode(request, analysis="x" * MAX_ANALYSIS_CHARS)
    phase_b = max_structured_billable(structured, output_cap=MAX_TOKENS_DECODE)
    expected = 2 * phase_a + phase_b
    assert max_billable_for_request(request) == expected


@pytest.mark.unit
def test_max_billable_suggest_known_prompt() -> None:
    request = SuggestRuleRequest(
        incoming="давай без сарказма в переписке",
        rules=(),
        relationship=RelationshipKind.PARTNER,
        deadline_seconds=45.0,
    )
    prepared = prepare_suggest_rule(request)
    expected = max_structured_billable(prepared, output_cap=MAX_TOKENS_SUGGEST)
    assert max_billable_for_request(request) == expected


@pytest.mark.unit
def test_adapter_max_billable_delegates_to_estimate() -> None:
    from typing import Any, cast

    from svoi_pravila.adapters.llm.gigachat.adapter import GigaChatTextGenerator
    from tests.factories import make_settings

    class _Client:
        async def acheck_connectivity(self) -> None:
            return None

    generator = GigaChatTextGenerator(cast(Any, _Client()), make_settings())
    request = RewriteRequest(
        draft="пожалуйста говори спокойнее",
        goal=None,
        rules=(),
        relationship=RelationshipKind.PARTNER,
        deadline_seconds=5.0,
    )
    assert generator.max_billable(request) == max_billable_for_request(request)
