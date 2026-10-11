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
from svoi_pravila.domain.ids import ContactId, UserId


@pytest.mark.unit
def test_compose_handoff_round_trip() -> None:
    user_id = UserId(UUID(int=7))
    with_contact = ComposeHandoffPayload(
        user_id=user_id,
        draft="привет\nещё",
        contact_id=ContactId(UUID(int=1)),
    )
    assert decode_compose_handoff_payload(encode_compose_handoff_payload(with_contact)) == (
        with_contact
    )
    without = ComposeHandoffPayload(user_id=user_id, draft="solo", contact_id=None)
    assert decode_compose_handoff_payload(encode_compose_handoff_payload(without)) == without


@pytest.mark.unit
def test_compose_handoff_decode_rejects_defects() -> None:
    good_user = str(UUID(int=1)).encode()
    with pytest.raises(ValueError):
        decode_compose_handoff_payload(b"\xff")
    with pytest.raises(ValueError):
        decode_compose_handoff_payload(b"no-newline")
    with pytest.raises(ValueError):
        decode_compose_handoff_payload(b"1\n" + good_user + b"\n\ntext")
    with pytest.raises(ValueError):
        decode_compose_handoff_payload(b"2\nonly-user")
    with pytest.raises(ValueError):
        decode_compose_handoff_payload(b"2\n" + good_user + b"\nno-draft-sep")
    with pytest.raises(ValueError):
        decode_compose_handoff_payload(b"2\nnot-a-uuid\n\ntext")
    with pytest.raises(ValueError):
        decode_compose_handoff_payload(b"2\n" + good_user + b"\nnot-a-uuid\ntext")


@pytest.mark.unit
def test_compose_handoff_ref_shape() -> None:
    assert is_compose_handoff_ref("h_" + ("a" * 64))
    assert not is_compose_handoff_ref("h_short")
    assert not is_compose_handoff_ref("p_" + ("a" * 64))
