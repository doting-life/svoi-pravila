"""Unit tests for compose-handoff payload encode/decode helpers."""

from __future__ import annotations

from uuid import UUID

import pytest

from svoi_pravila.application.compose_handoff import (
    ComposeHandoffPayload,
    decode_compose_handoff_payload,
    encode_compose_handoff_payload,
    is_compose_handoff_ref,
)
from svoi_pravila.domain.ids import UserId


@pytest.mark.unit
def test_compose_handoff_round_trip_keeps_draft_lines() -> None:
    payload = ComposeHandoffPayload(user_id=UserId(UUID(int=7)), draft="привет\nещё")
    assert decode_compose_handoff_payload(encode_compose_handoff_payload(payload)) == payload


@pytest.mark.unit
def test_compose_handoff_decode_rejects_defects() -> None:
    good_user = str(UUID(int=1)).encode()
    with pytest.raises(ValueError):
        decode_compose_handoff_payload(b"\xff")
    with pytest.raises(ValueError):
        decode_compose_handoff_payload(b"no-newline")
    with pytest.raises(ValueError):
        decode_compose_handoff_payload(b"2\n" + good_user + b"\n\ntext")
    with pytest.raises(ValueError):
        decode_compose_handoff_payload(b"3\nonly-user")
    with pytest.raises(ValueError):
        decode_compose_handoff_payload(b"3\nnot-a-uuid\ntext")


@pytest.mark.unit
def test_compose_handoff_ref_shape() -> None:
    assert is_compose_handoff_ref("h_" + ("a" * 64))
    assert not is_compose_handoff_ref("h_short")
    assert not is_compose_handoff_ref("p_" + ("a" * 64))
