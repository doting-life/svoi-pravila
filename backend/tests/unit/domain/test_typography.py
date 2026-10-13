"""Human typography (ADR-0015 §5)."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from svoi_pravila.domain.typography import humanize_typography

_EM = "\u2014"
_EN = "\u2013"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (f"Привет {_EM} как дела", "Привет, как дела"),
        (f"Привет {_EN} как дела", "Привет, как дела"),
        (f"С 10{_EN}12 часов", "С 10-12 часов"),
        (f"С 10 {_EM} 12 часов", "С 10-12 часов"),
        (f"слово{_EM}слово", "слово-слово"),
        (f"слово{_EN}слово", "слово-слово"),
        ("Ну…", "Ну..."),
        ("Привет  мир", "Привет мир"),
        ("Привет,, мир", "Привет, мир"),
        ("Привет,.", "Привет."),
        (f"Привет, {_EM} мир", "Привет, мир"),
        ("Обычный текст.", "Обычный текст."),
        ("", ""),
    ],
)
def test_dash_free_source_is_humanized(text: str, expected: str) -> None:
    assert humanize_typography("без тире", text) == expected


@pytest.mark.unit
@pytest.mark.parametrize("dash", [_EM, _EN])
def test_source_with_dash_keeps_text_unchanged(dash: str) -> None:
    text = f"Привет {_EM} как дела… {_EN} ну"
    assert humanize_typography(f"Я {dash} за", text) == text


@pytest.mark.unit
def test_plain_hyphen_in_source_does_not_count_as_a_dash() -> None:
    assert humanize_typography("кто-то", f"Привет {_EM} мир") == "Привет, мир"


@pytest.mark.unit
@given(text=st.text(max_size=200))
def test_result_has_no_dash_or_ellipsis_and_is_idempotent(text: str) -> None:
    once = humanize_typography("", text)
    assert _EM not in once
    assert _EN not in once
    assert "\u2026" not in once
    assert humanize_typography("", once) == once


@pytest.mark.unit
@given(source=st.text(max_size=50), text=st.text(max_size=100))
def test_dash_in_source_always_wins(source: str, text: str) -> None:
    with_dash = source + _EM
    assert humanize_typography(with_dash, text) == text
