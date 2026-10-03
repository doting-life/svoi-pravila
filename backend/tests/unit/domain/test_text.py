"""Value object tests for text.py and related identifiers."""

from __future__ import annotations

import unicodedata

import pytest
from hypothesis import given
from hypothesis import strategies as st

from svoi_pravila.domain.errors import InvalidValueError
from svoi_pravila.domain.ids import TelegramUserId
from svoi_pravila.domain.invite import InviteTokenHash
from svoi_pravila.domain.text import ContactLabel, RuleText, Sha256Hex


@pytest.mark.unit
def test_rule_text_strips_and_nfc() -> None:
    composed = unicodedata.normalize("NFC", "café")
    decomposed = unicodedata.normalize("NFD", "café")
    assert RuleText(f"  {decomposed}  ").value == composed


@pytest.mark.unit
def test_rule_text_rejects_empty_and_control() -> None:
    with pytest.raises(InvalidValueError):
        RuleText("   ")
    with pytest.raises(InvalidValueError):
        RuleText("a\x00b")
    with pytest.raises(InvalidValueError):
        RuleText("x" * 281)


@pytest.mark.unit
def test_contact_label_bounds() -> None:
    assert ContactLabel("a").value == "a"
    with pytest.raises(InvalidValueError):
        ContactLabel("x" * 33)


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


@given(st.text(min_size=1, max_size=280).filter(lambda s: s.strip() != ""))
@pytest.mark.unit
def test_rule_text_normalization_idempotent(raw: str) -> None:
    try:
        first = RuleText(raw)
    except InvalidValueError:
        return
    second = RuleText(first.value)
    assert first.value == second.value
    assert 1 <= len(first.value) <= 280


@given(st.text(min_size=1, max_size=32).filter(lambda s: s.strip() != ""))
@pytest.mark.unit
def test_contact_label_normalization_idempotent(raw: str) -> None:
    try:
        first = ContactLabel(raw)
    except InvalidValueError:
        return
    second = ContactLabel(first.value)
    assert first.value == second.value
