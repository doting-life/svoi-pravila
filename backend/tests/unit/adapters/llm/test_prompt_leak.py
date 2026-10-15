"""Prompt-leak window membership."""

from __future__ import annotations

from typing import Any, cast

import pytest

from svoi_pravila.adapters.llm.gigachat.prompt_leak import (
    LEAK_WINDOW,
    collect_text_fields,
    prompt_leak_reason,
    system_prompt_windows,
)
from svoi_pravila.application.errors import InvalidOutputReason

_MARKER = "SPBOUND_allocatedmarkervalue"


@pytest.mark.unit
def test_windows_and_short_phrase_pass() -> None:
    system = "alpha " * 20 + "unique-instruction-block-for-the-model-please"
    windows = system_prompt_windows(system)
    assert all(len(item) == LEAK_WINDOW for item in windows)
    assert (
        prompt_leak_reason(["please be kind"], windows=windows, marker=_MARKER, untrusted=())
        is None
    )
    leaked = next(window for window in windows if window[0].isalnum() and window[-1].isalnum())
    assert (
        prompt_leak_reason([leaked], windows=windows, marker=_MARKER, untrusted=())
        is InvalidOutputReason.PROMPT_LEAK
    )
    assert (
        prompt_leak_reason(
            ["prefix SPBOUND_zzz suffix"], windows=windows, marker=_MARKER, untrusted=()
        )
        is InvalidOutputReason.PROMPT_LEAK
    )


@pytest.mark.unit
def test_collect_text_fields_nested() -> None:
    assert collect_text_fields({"a": [{"b": "x"}, "y"], "c": 1}) == ("x", "y")


@pytest.mark.unit
def test_short_system_prompt_has_no_windows() -> None:
    assert system_prompt_windows("short") == frozenset()
    assert (
        prompt_leak_reason(["also short"], windows=frozenset(), marker=_MARKER, untrusted=())
        is None
    )


_SYSTEM = "alpha " * 20 + "unique-instruction-block-for-the-model-please"


def _windows_with_alnum_edges() -> list[str]:
    return sorted(
        window
        for window in system_prompt_windows(_SYSTEM)
        if window[0].isalnum() and window[-1].isalnum()
    )


def _window() -> str:
    return _windows_with_alnum_edges()[0]


@pytest.mark.unit
def test_branch_a_allocated_marker_is_always_a_leak() -> None:
    windows = system_prompt_windows(_SYSTEM)
    assert (
        prompt_leak_reason([f"x {_MARKER} y"], windows=windows, marker=_MARKER, untrusted=())
        is InvalidOutputReason.PROMPT_LEAK
    )
    assert prompt_leak_reason(["x y"], windows=windows, marker=_MARKER, untrusted=()) is None


@pytest.mark.unit
def test_branch_a_marker_is_a_leak_even_when_the_prefix_is_untrusted() -> None:
    windows = system_prompt_windows(_SYSTEM)
    assert (
        prompt_leak_reason(
            [f"x {_MARKER}"],
            windows=windows,
            marker=_MARKER,
            untrusted=["draft with SPBOUND_ inside"],
        )
        is InvalidOutputReason.PROMPT_LEAK
    )


@pytest.mark.unit
def test_branch_b_prefix_is_a_leak_unless_an_untrusted_text_contains_it() -> None:
    windows = system_prompt_windows(_SYSTEM)
    field = "the draft says SPBOUND_ so I keep it"
    assert (
        prompt_leak_reason([field], windows=windows, marker=_MARKER, untrusted=["plain draft"])
        is InvalidOutputReason.PROMPT_LEAK
    )
    assert (
        prompt_leak_reason(
            [field],
            windows=windows,
            marker=_MARKER,
            untrusted=["plain draft", "my draft mentions SPBOUND_ itself"],
        )
        is None
    )


@pytest.mark.unit
def test_branch_c_window_is_a_leak_unless_an_untrusted_text_contains_it() -> None:
    windows = system_prompt_windows(_SYSTEM)
    window = _window()
    assert (
        prompt_leak_reason([window], windows=windows, marker=_MARKER, untrusted=["other draft"])
        is InvalidOutputReason.PROMPT_LEAK
    )
    quoted = f"I pasted:   {window.replace(' ', '  ')} end"
    assert prompt_leak_reason([window], windows=windows, marker=_MARKER, untrusted=[quoted]) is None


@pytest.mark.unit
def test_branch_c_one_untrusted_window_does_not_excuse_another_one() -> None:
    windows = system_prompt_windows(_SYSTEM)
    ordered = _windows_with_alnum_edges()
    first, other = ordered[0], ordered[-1]
    assert first not in other
    assert (
        prompt_leak_reason(
            [f"{first} / {other}"], windows=windows, marker=_MARKER, untrusted=[first]
        )
        is InvalidOutputReason.PROMPT_LEAK
    )


@pytest.mark.unit
def test_marker_and_untrusted_are_required_keyword_arguments() -> None:
    windows = system_prompt_windows(_SYSTEM)
    call = cast(Any, prompt_leak_reason)
    with pytest.raises(TypeError):
        call(["x"], windows=windows)
    with pytest.raises(TypeError):
        call(["x"], windows=windows, marker=_MARKER)
    with pytest.raises(TypeError):
        call(["x"], windows=windows, untrusted=())
