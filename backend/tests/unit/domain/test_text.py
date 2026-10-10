"""Value object tests for text.py and related identifiers."""

from __future__ import annotations

import unicodedata

import pytest
from hypothesis import given
from hypothesis import strategies as st

from svoi_pravila.domain.errors import (
    InvalidValueError,
    RuleTextEmptyError,
    RuleTextInvalidCharsError,
    RuleTextTooLongError,
)
from svoi_pravila.domain.ids import TelegramUserId
from svoi_pravila.domain.invite import InviteTokenHash
from svoi_pravila.domain.text import RULE_TEXT_MAX_CHARS, ContactLabel, RuleText, Sha256Hex


@pytest.mark.unit
def test_rule_text_strips_and_nfc() -> None:
    composed = unicodedata.normalize("NFC", "café")
    decomposed = unicodedata.normalize("NFD", "café")
    assert RuleText(f"  {decomposed}  ").value == composed


@pytest.mark.unit
def test_rule_text_rejects_empty() -> None:
    with pytest.raises(RuleTextEmptyError):
        RuleText("   ")
    with pytest.raises(RuleTextEmptyError):
        RuleText("\n\n")


@pytest.mark.unit
def test_rule_text_rejects_control_chars() -> None:
    with pytest.raises(RuleTextInvalidCharsError):
        RuleText("a\x00b")
    with pytest.raises(RuleTextInvalidCharsError):
        RuleText("a\tb")


@pytest.mark.unit
def test_rule_text_length_bounds() -> None:
    assert len(RuleText("x" * RULE_TEXT_MAX_CHARS).value) == RULE_TEXT_MAX_CHARS
    with pytest.raises(RuleTextTooLongError) as exc_info:
        RuleText("x" * (RULE_TEXT_MAX_CHARS + 1))
    assert exc_info.value.max == RULE_TEXT_MAX_CHARS
    assert exc_info.value.actual == RULE_TEXT_MAX_CHARS + 1


@pytest.mark.unit
def test_rule_text_allows_and_normalizes_newlines() -> None:
    assert RuleText("line1\nline2").value == "line1\nline2"
    assert RuleText("a\r\nb").value == "a\nb"
    assert RuleText("a\rb").value == "a\nb"
    assert RuleText("a\r\n\rb").value == "a\n\nb"


@pytest.mark.unit
def test_rule_text_collapses_blank_lines() -> None:
    assert RuleText("a\n\n\nb").value == "a\n\nb"
    assert RuleText("a\n\n\n\nb").value == "a\n\nb"
    assert RuleText("a\n\nb").value == "a\n\nb"


@pytest.mark.unit
def test_rule_text_strips_trailing_spaces_per_line() -> None:
    assert RuleText("hello  \nworld   ").value == "hello\nworld"
    assert RuleText("a \t\nb \t").value == "a\nb"


@pytest.mark.unit
def test_rule_text_emoji_counts_as_one_code_point() -> None:
    emoji = "😀"
    assert len(emoji) == 1
    text = "x" * (RULE_TEXT_MAX_CHARS - 1) + emoji
    assert len(RuleText(text).value) == RULE_TEXT_MAX_CHARS
    with pytest.raises(RuleTextTooLongError) as exc_info:
        RuleText(text + "y")
    assert exc_info.value.actual == RULE_TEXT_MAX_CHARS + 1


@pytest.mark.unit
def test_contact_label_bounds() -> None:
    assert ContactLabel("a").value == "a"
    with pytest.raises(InvalidValueError):
        ContactLabel("x" * 33)


@pytest.mark.unit
def test_contact_label_rejects_newlines() -> None:
    with pytest.raises(InvalidValueError):
        ContactLabel("a\nb")


@pytest.mark.unit
def test_telegram_user_id_positive() -> None:
    assert TelegramUserId(1).value == 1
    with pytest.raises(InvalidValueError):
        TelegramUserId(0)


@pytest.mark.unit
def test_sha256_and_invite_token_hash() -> None:
    digest = Sha256Hex("a" * 64)
    assert digest.value == "a" * 64
    with pytest.raises(InvalidValueError):
        Sha256Hex("not-hex")
    h = InviteTokenHash.from_raw_token("secret")
    assert len(h.hex) == 64
    with pytest.raises(InvalidValueError):
        InviteTokenHash.from_hex("not-hex")


@given(st.text(min_size=1, max_size=RULE_TEXT_MAX_CHARS).filter(lambda s: s.strip() != ""))
@pytest.mark.unit
def test_rule_text_normalization_idempotent(raw: str) -> None:
    try:
        first = RuleText(raw)
    except InvalidValueError:
        return
    second = RuleText(first.value)
    assert first.value == second.value
    assert 1 <= len(first.value) <= RULE_TEXT_MAX_CHARS


@given(st.text(min_size=1, max_size=32).filter(lambda s: s.strip() != ""))
@pytest.mark.unit
def test_contact_label_normalization_idempotent(raw: str) -> None:
    try:
        first = ContactLabel(raw)
    except InvalidValueError:
        return
    second = ContactLabel(first.value)
    assert first.value == second.value
