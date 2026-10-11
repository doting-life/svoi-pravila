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
from svoi_pravila.domain.ids import ContactId


@pytest.mark.unit
def test_compose_handoff_round_trip() -> None:
    with_contact = ComposeHandoffPayload(draft="привет\nещё", contact_id=ContactId(UUID(int=1)))
    assert decode_compose_handoff_payload(encode_compose_handoff_payload(with_contact)) == (
        with_contact
    )
    without = ComposeHandoffPayload(draft="solo", contact_id=None)
    assert decode_compose_handoff_payload(encode_compose_handoff_payload(without)) == without
    assert decode_compose_handoff_payload(b"\nempty-contact") == ComposeHandoffPayload(
        draft="empty-contact", contact_id=None
    )


@pytest.mark.unit
def test_compose_handoff_decode_rejects_defects() -> None:
    with pytest.raises(ValueError):
        decode_compose_handoff_payload(b"\xff")
    with pytest.raises(ValueError):
        decode_compose_handoff_payload(b"no-newline")
    with pytest.raises(ValueError):
        decode_compose_handoff_payload(b"not-a-uuid\ntext")


@pytest.mark.unit
def test_compose_handoff_ref_shape() -> None:
    assert is_compose_handoff_ref("h_" + ("a" * 64))
    assert not is_compose_handoff_ref("h_short")
    assert not is_compose_handoff_ref("p_" + ("a" * 64))
