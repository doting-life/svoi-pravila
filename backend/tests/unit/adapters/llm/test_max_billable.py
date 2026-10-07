"""Known-prompt max_billable values for each generation operation."""

from __future__ import annotations

import pytest

from svoi_pravila.adapters.llm.gigachat.estimate import (
    estimate_prepared_tokens,
    max_billable_for_request,
)
from svoi_pravila.adapters.llm.gigachat.prepared import (
    prepare_decode,
    prepare_decode_analysis,
    prepare_help_say,
    prepare_soften,
    prepare_suggest_rule,
)
from svoi_pravila.adapters.llm.gigachat.validation import (
    MAX_ANALYSIS_CHARS,
    MAX_TOKENS_ANALYSIS,
    MAX_TOKENS_DECODE,
    MAX_TOKENS_HELP_SAY,
    MAX_TOKENS_SOFTEN,
    MAX_TOKENS_SUGGEST,
)
from svoi_pravila.application.ports.generation import (
    DecodeRequest,
    HelpSayIntent,
    HelpSayRequest,
    SoftenRequest,
    SuggestRuleRequest,
)
from svoi_pravila.domain.enums import RelationshipKind


@pytest.mark.unit
def test_max_billable_soften_known_prompt() -> None:
    request = SoftenRequest(
        draft="пожалуйста говори спокойнее",
        rules=(),
        relationship=RelationshipKind.PARTNER,
        deadline_seconds=5.0,
    )
    prepared = prepare_soften(request)
    expected = 2 * estimate_prepared_tokens(
        prepared.system, prepared.user, output_cap=MAX_TOKENS_SOFTEN
    )
    assert max_billable_for_request(request) == expected


@pytest.mark.unit
def test_max_billable_help_say_known_prompt() -> None:
    request = HelpSayRequest(
        intent=HelpSayIntent.DECLINE,
        details="мне нужно отказать вежливо",
        rules=(),
        relationship=RelationshipKind.FRIEND,
        deadline_seconds=5.0,
    )
    prepared = prepare_help_say(request)
    expected = 2 * estimate_prepared_tokens(
        prepared.system, prepared.user, output_cap=MAX_TOKENS_HELP_SAY
    )
    assert max_billable_for_request(request) == expected


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
    phase_b = estimate_prepared_tokens(
        structured.system, structured.user, output_cap=MAX_TOKENS_DECODE
    )
    expected = 2 * phase_a + 2 * phase_b
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
    expected = 2 * estimate_prepared_tokens(
        prepared.system, prepared.user, output_cap=MAX_TOKENS_SUGGEST
    )
    assert max_billable_for_request(request) == expected
