"""Token-budget estimates for GigaChat calls (shared by adapter and benchmarks)."""

from __future__ import annotations

import math

from svoi_pravila.adapters.llm.gigachat.attempt_policy import MAX_ATTEMPTS
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
    HelpSayRequest,
    SoftenRequest,
    SuggestRuleRequest,
)


def chars_to_tokens(text: str) -> int:
    """Heuristic: ceil(characters / 3), matching GigaChat average density docs."""
    if not text:
        return 0
    return math.ceil(len(text) / 3)


def estimate_prepared_tokens(system: str, user: str, *, output_cap: int) -> int:
    """Estimate billable tokens for one provider call from rendered messages."""
    return chars_to_tokens(system) + chars_to_tokens(user) + output_cap


def max_billable_for_request(
    request: SoftenRequest | HelpSayRequest | DecodeRequest | SuggestRuleRequest,
) -> int:
    """Worst-case billable tokens for ``request``, including retries."""
    if isinstance(request, SoftenRequest):
        prepared = prepare_soften(request)
        one = estimate_prepared_tokens(prepared.system, prepared.user, output_cap=MAX_TOKENS_SOFTEN)
        return MAX_ATTEMPTS * one
    if isinstance(request, HelpSayRequest):
        prepared = prepare_help_say(request)
        one = estimate_prepared_tokens(
            prepared.system, prepared.user, output_cap=MAX_TOKENS_HELP_SAY
        )
        return MAX_ATTEMPTS * one
    if isinstance(request, DecodeRequest):
        analysis = prepare_decode_analysis(request)
        phase_a = estimate_prepared_tokens(
            analysis.system, analysis.user, output_cap=MAX_TOKENS_ANALYSIS
        )
        structured = prepare_decode(request, analysis="x" * MAX_ANALYSIS_CHARS)
        phase_b = estimate_prepared_tokens(
            structured.system, structured.user, output_cap=MAX_TOKENS_DECODE
        )
        return MAX_ATTEMPTS * phase_a + MAX_ATTEMPTS * phase_b
    prepared = prepare_suggest_rule(request)
    one = estimate_prepared_tokens(prepared.system, prepared.user, output_cap=MAX_TOKENS_SUGGEST)
    return MAX_ATTEMPTS * one
