"""Token-budget estimates for GigaChat calls (shared by adapter and benchmarks)."""

from __future__ import annotations

import math

from svoi_pravila.adapters.llm.gigachat.attempt_policy import MAX_ATTEMPTS
from svoi_pravila.adapters.llm.gigachat.prepared import (
    PreparedMessages,
    prepare_decode,
    prepare_decode_analysis,
    prepare_rewrite,
    prepare_suggest_rule,
)
from svoi_pravila.adapters.llm.gigachat.repair import longest_correction_chars
from svoi_pravila.adapters.llm.gigachat.validation import (
    CHARS_PER_TOKEN,
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


def chars_to_tokens(text: str) -> int:
    """Heuristic: ceil(characters / 3), matching GigaChat average density docs."""
    if not text:
        return 0
    return math.ceil(len(text) / 3)


def estimate_prepared_tokens(system: str, user: str, *, output_cap: int) -> int:
    """Estimate billable tokens for one provider call from rendered messages."""
    return chars_to_tokens(system) + chars_to_tokens(user) + output_cap


def max_structured_billable(prepared: PreparedMessages, *, output_cap: int) -> int:
    """Worst-case billable tokens of one structured call with its corrective retry.

    The second request repeats system and user, adds the first answer at the output cap
    and the longest correction line, and may again produce ``output_cap`` tokens.
    """
    first = estimate_prepared_tokens(prepared.system, prepared.user, output_cap=output_cap)
    second = first + output_cap + math.ceil(longest_correction_chars() / CHARS_PER_TOKEN)
    return first + second


def max_billable_for_request(
    request: RewriteRequest | DecodeRequest | SuggestRuleRequest,
) -> int:
    """Worst-case billable tokens for ``request``, including retries."""
    if isinstance(request, RewriteRequest):
        return max_structured_billable(prepare_rewrite(request), output_cap=MAX_TOKENS_REWRITE)
    if isinstance(request, DecodeRequest):
        analysis = prepare_decode_analysis(request)
        phase_a = estimate_prepared_tokens(
            analysis.system, analysis.user, output_cap=MAX_TOKENS_ANALYSIS
        )
        structured = prepare_decode(request, analysis="x" * MAX_ANALYSIS_CHARS)
        phase_b = max_structured_billable(structured, output_cap=MAX_TOKENS_DECODE)
        return MAX_ATTEMPTS * phase_a + phase_b
    return max_structured_billable(prepare_suggest_rule(request), output_cap=MAX_TOKENS_SUGGEST)
