"""Property and example tests for normalize_inline_text."""

from __future__ import annotations

import unicodedata

import pytest
from hypothesis import given
from hypothesis import strategies as st

from svoi_pravila.application.inline_text import normalize_inline_text


@pytest.mark.unit
def test_normalize_collapses_horizontal_whitespace_and_keeps_newlines() -> None:
    assert normalize_inline_text("  a \t\u00a0 b  \n  c  ") == "a b\n c"
    assert normalize_inline_text("\n\nhello\n\n") == "hello"


@pytest.mark.unit
def test_normalize_nfc_nfd_equivalence_example() -> None:
    nfd = unicodedata.normalize("NFD", "café draft here")
    nfc = unicodedata.normalize("NFC", "café draft here")
    assert nfd != nfc
    assert normalize_inline_text(nfd) == normalize_inline_text(nfc)


@pytest.mark.unit
def test_normalize_whitespace_variants_map_to_one_form() -> None:
    forms = (
        "please leave me alone",
        "  please leave me alone  ",
        "please\tleave\u00a0me alone",
        "please  leave   me alone",
    )
    normalized = {normalize_inline_text(form) for form in forms}
    assert normalized == {"please leave me alone"}


@pytest.mark.unit
@given(st.text(max_size=80))
def test_normalize_idempotent(text: str) -> None:
    once = normalize_inline_text(text)
    assert normalize_inline_text(once) == once


@pytest.mark.unit
@given(st.text(min_size=1, max_size=40))
def test_normalize_nfc_nfd_equivalence(text: str) -> None:
    nfd = unicodedata.normalize("NFD", text)
    nfc = unicodedata.normalize("NFC", text)
    assert normalize_inline_text(nfd) == normalize_inline_text(nfc)
