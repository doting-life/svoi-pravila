"""Compose session payload helpers."""

from __future__ import annotations

from uuid import UUID

import pytest

from svoi_pravila.application.compose_session import (
    ComposeSessionPayload,
    decode_compose_session_payload,
    encode_compose_session_payload,
    is_compose_session_ref,
)
from svoi_pravila.domain.ids import ContactId, UserId


@pytest.mark.unit
def test_compose_session_round_trip() -> None:
    user_id = UserId(UUID(int=9))
    with_contact = ComposeSessionPayload(user_id=user_id, contact_id=ContactId(UUID(int=2)))
    assert decode_compose_session_payload(encode_compose_session_payload(with_contact)) == (
        with_contact
    )
    without = ComposeSessionPayload(user_id=user_id, contact_id=None)
    assert decode_compose_session_payload(encode_compose_session_payload(without)) == without


@pytest.mark.unit
def test_compose_session_ref_and_defects() -> None:
    good_user = str(UUID(int=1)).encode()
    assert is_compose_session_ref("cs_" + ("b" * 64))
    assert not is_compose_session_ref("h_" + ("b" * 64))
    with pytest.raises(ValueError):
        decode_compose_session_payload(b"\xff")
    with pytest.raises(ValueError):
        decode_compose_session_payload(b"2\nid\n")
    with pytest.raises(ValueError):
        decode_compose_session_payload(b"1\nonly-user")
    with pytest.raises(ValueError):
        decode_compose_session_payload(b"1\nnot-a-uuid\n")
    with pytest.raises(ValueError):
        decode_compose_session_payload(b"1\n" + good_user + b"\nnot-a-uuid")
