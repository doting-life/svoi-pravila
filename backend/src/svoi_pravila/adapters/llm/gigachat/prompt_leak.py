"""Reject outputs that echo the system prompt or boundary markers."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence

from svoi_pravila.adapters.llm.gigachat.boundaries import BOUNDARY_PREFIX
from svoi_pravila.application.errors import InvalidOutputReason

LEAK_WINDOW = 40


def normalize_prompt_whitespace(text: str) -> str:
    """Collapse whitespace for verbatim-leak comparison."""
    return " ".join(text.split())


def system_prompt_windows(system_prompt: str) -> frozenset[str]:
    """Rolling windows of length ``LEAK_WINDOW`` over the normalized system prompt."""
    normalized = normalize_prompt_whitespace(system_prompt)
    if len(normalized) < LEAK_WINDOW:
        return frozenset()
    limit = len(normalized) - LEAK_WINDOW + 1
    return frozenset(normalized[index : index + LEAK_WINDOW] for index in range(limit))


def collect_text_fields(value: object) -> tuple[str, ...]:
    """Collect string leaves from parsed JSON-like structures."""
    found: list[str] = []
    _collect(value, found)
    return tuple(found)


def _collect(value: object, found: list[str]) -> None:
    if isinstance(value, str):
        found.append(value)
        return
    if isinstance(value, Mapping):
        for item in value.values():
            _collect(item, found)
        return
    if isinstance(value, Sequence) and not isinstance(value, bytes | bytearray):
        for item in value:
            _collect(item, found)


def prompt_leak_reason(
    texts: Iterable[str],
    *,
    windows: frozenset[str],
    marker: str,
    untrusted: Sequence[str],
) -> InvalidOutputReason | None:
    """Return ``PROMPT_LEAK`` when a field echoes the marker or the system prompt.

    (a) A field containing the allocated ``marker`` is a leak. (b) A field containing the
    boundary prefix is a leak unless the prefix occurs in an untrusted text (the draft may
    quote it). (c) A 40-character system-prompt window is a leak unless the same window
    occurs, normalized, in an untrusted text.
    """
    prefix_is_untrusted = any(BOUNDARY_PREFIX in text for text in untrusted)
    normalized_untrusted = tuple(normalize_prompt_whitespace(text) for text in untrusted)
    for text in texts:
        if marker in text:
            return InvalidOutputReason.PROMPT_LEAK
        if BOUNDARY_PREFIX in text and not prefix_is_untrusted:
            return InvalidOutputReason.PROMPT_LEAK
        if _echoes_prompt_window(text, windows, normalized_untrusted):
            return InvalidOutputReason.PROMPT_LEAK
    return None


def _echoes_prompt_window(
    text: str, windows: frozenset[str], normalized_untrusted: Sequence[str]
) -> bool:
    normalized = normalize_prompt_whitespace(text)
    if len(normalized) < LEAK_WINDOW or not windows:
        return False
    limit = len(normalized) - LEAK_WINDOW + 1
    for index in range(limit):
        window = normalized[index : index + LEAK_WINDOW]
        if window in windows and not any(window in item for item in normalized_untrusted):
            return True
    return False
