"""Unit tests for rule-source payload encode/decode helpers."""

from __future__ import annotations

from uuid import UUID

import pytest

from svoi_pravila.application.rule_source import (
    RuleSourcePayload,
    decode_rule_source_payload,
    encode_rule_source_payload,
    rule_source_callback_data,
)
from svoi_pravila.domain.ids import ContactId


@pytest.mark.unit
def test_rule_source_round_trip() -> None:
    payload = RuleSourcePayload(contact_id=ContactId(UUID(int=1)), incoming_text="привет\nещё")
    assert decode_rule_source_payload(encode_rule_source_payload(payload)) == payload


@pytest.mark.unit
def test_rule_source_decode_rejects_defects() -> None:
    with pytest.raises(ValueError):
        decode_rule_source_payload(b"\xff")
    with pytest.raises(ValueError):
        decode_rule_source_payload(b"no-newline")
    with pytest.raises(ValueError):
        decode_rule_source_payload(b"\ntext")
    with pytest.raises(ValueError):
        decode_rule_source_payload(b"not-a-uuid\ntext")


@pytest.mark.unit
def test_rule_source_callback_rejects_oversized_token() -> None:
    assert rule_source_callback_data("short") == "sn:short"
    with pytest.raises(ValueError, match="64 bytes"):
        rule_source_callback_data("x" * 62)
