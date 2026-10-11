"""Deterministic rule-leak and rule-effect checks (no LLM judge)."""

from __future__ import annotations

import re

_REQUEST_MARKERS: tuple[str, ...] = (
    "давай",
    "можешь",
    "можем",
    "прошу",
    "пожалуйста",
)
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?…])\s+")


def variant_leaks_stems(
    *,
    draft: str,
    variants: tuple[str, ...],
    stop_stems: tuple[str, ...],
) -> bool:
    """True when any variant contains a stop-stem absent from the draft."""
    draft_folded = draft.casefold()
    for variant in variants:
        text = variant.casefold()
        for stem in stop_stems:
            needle = stem.casefold()
            if not needle:
                continue
            if needle in text and needle not in draft_folded:
                return True
    return False


def variant_complies_with_closing_ask(text: str) -> bool:
    """True when the last sentence ends with ``?`` or contains a request marker."""
    stripped = text.strip()
    if not stripped:
        return False
    parts = [part.strip() for part in _SENTENCE_SPLIT.split(stripped) if part.strip()]
    last = parts[-1] if parts else stripped
    if last.endswith("?"):
        return True
    folded = last.casefold()
    return any(marker in folded for marker in _REQUEST_MARKERS)


def variants_comply_with_closing_ask(variants: tuple[str, ...]) -> bool:
    """True when every variant complies with the closing-ask form rule."""
    if not variants:
        return False
    return all(variant_complies_with_closing_ask(item) for item in variants)
