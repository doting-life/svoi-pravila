"""Prompt-leak window membership."""

from __future__ import annotations

import pytest

from svoi_pravila.adapters.llm.gigachat.prompt_leak import (
    LEAK_WINDOW,
    collect_text_fields,
    prompt_leak_reason,
    system_prompt_windows,
)
from svoi_pravila.application.errors import InvalidOutputReason


@pytest.mark.unit
def test_windows_and_short_phrase_pass() -> None:
    system = "alpha " * 20 + "unique-instruction-block-for-the-model-please"
    windows = system_prompt_windows(system)
    assert all(len(item) == LEAK_WINDOW for item in windows)
    assert prompt_leak_reason(["please be kind"], windows=windows) is None
    leaked = next(window for window in windows if window[0].isalnum() and window[-1].isalnum())
    assert prompt_leak_reason([leaked], windows=windows) is InvalidOutputReason.PROMPT_LEAK
    assert (
        prompt_leak_reason(["prefix SPBOUND_zzz suffix"], windows=windows)
        is InvalidOutputReason.PROMPT_LEAK
    )


@pytest.mark.unit
def test_collect_text_fields_nested() -> None:
    assert collect_text_fields({"a": [{"b": "x"}, "y"], "c": 1}) == ("x", "y")


@pytest.mark.unit
def test_short_system_prompt_has_no_windows() -> None:
    assert system_prompt_windows("short") == frozenset()
    assert prompt_leak_reason(["also short"], windows=frozenset()) is None
