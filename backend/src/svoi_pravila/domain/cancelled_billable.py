"""Conservative billable-token estimate when a generation is cancelled mid-flight."""

from __future__ import annotations

import math


def estimate_cancelled_billable(input_chars: int, max_output_tokens: int) -> int:
    """Upper-bound billable tokens for a call cancelled after the provider started.

    GigaChat tokenization of Cyrillic is denser than the adapter's average
    ``CHARS_PER_TOKEN = 3`` used for output caps. Dividing input length by 2
    over-estimates input tokens for Cyrillic (and is still safe for Latin).
    The output part is the operation's ``max_tokens`` cap — the same value the
    adapter passes to the provider — because a cancelled stream may have
    consumed up to that many completion tokens.
    """
    if input_chars < 0:
        msg = "input_chars must be non-negative"
        raise ValueError(msg)
    if max_output_tokens < 0:
        msg = "max_output_tokens must be non-negative"
        raise ValueError(msg)
    return math.ceil(input_chars / 2) + max_output_tokens
