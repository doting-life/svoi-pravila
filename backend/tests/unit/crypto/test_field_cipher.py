"""FieldCipher and DEK wrapping tests."""

from __future__ import annotations

import os
from uuid import UUID

import pytest
from hypothesis import given
from hypothesis import strategies as st

from svoi_pravila.crypto import (
    DecryptionError,
    FieldCipher,
    generate_dek,
    unwrap_dek,
    wrap_dek,
)

KEK = b"\x11" * 32
OWNER = UUID(int=7)


@pytest.mark.unit
def test_encrypt_round_trip() -> None:
    cipher = FieldCipher(KEK)
    aad = b"contacts:label:x:v1"
    blob = cipher.encrypt(b"secret", aad=aad)
    assert cipher.decrypt(blob, aad=aad) == b"secret"


@pytest.mark.unit
def test_two_encryptions_differ() -> None:
    cipher = FieldCipher(KEK)
    aad = b"aad"
    first = cipher.encrypt(b"same", aad=aad)
    second = cipher.encrypt(b"same", aad=aad)
    assert first != second


@pytest.mark.unit
def test_wrong_key_raises() -> None:
    blob = FieldCipher(KEK).encrypt(b"x", aad=b"a")
    with pytest.raises(DecryptionError):
        FieldCipher(b"\x22" * 32).decrypt(blob, aad=b"a")


@pytest.mark.unit
def test_tampered_inputs_raise() -> None:
    cipher = FieldCipher(KEK)
    aad = b"bound"
    blob = bytearray(cipher.encrypt(b"payload", aad=aad))
    cases = [
        bytes([0x02]) + bytes(blob[1:]),
        bytes(blob[:5]) + bytes([(blob[5] ^ 0x01)]) + bytes(blob[6:]),
        bytes(blob[:-1]) + bytes([blob[-1] ^ 0x01]),
        bytes(blob),
        b"\x01short",
    ]
    with pytest.raises(DecryptionError):
        cipher.decrypt(cases[0], aad=aad)
    with pytest.raises(DecryptionError):
        cipher.decrypt(cases[1], aad=aad)
    with pytest.raises(DecryptionError):
        cipher.decrypt(cases[2], aad=aad)
    with pytest.raises(DecryptionError):
        cipher.decrypt(cases[3], aad=b"other-aad")
    with pytest.raises(DecryptionError):
        cipher.decrypt(cases[4], aad=aad)


@pytest.mark.unit
def test_decryption_error_has_no_input_content() -> None:
    err = DecryptionError()
    assert "payload" not in str(err)
    assert str(err) == "decryption failed"


@pytest.mark.unit
def test_key_length_validated() -> None:
    with pytest.raises(ValueError):
        FieldCipher(b"short")


@pytest.mark.unit
def test_dek_wrap_round_trip() -> None:
    dek = generate_dek()
    wrapped = wrap_dek(KEK, dek, owner_kind="user", owner_id=OWNER)
    assert unwrap_dek(KEK, wrapped, owner_kind="user", owner_id=OWNER) == dek
    with pytest.raises(DecryptionError):
        unwrap_dek(KEK, wrapped, owner_kind="pair", owner_id=OWNER)


@given(st.binary(min_size=0, max_size=64), st.binary(min_size=1, max_size=32))
@pytest.mark.unit
def test_hypothesis_round_trip(plaintext: bytes, aad: bytes) -> None:
    key = os.urandom(32)
    cipher = FieldCipher(key)
    blob = cipher.encrypt(plaintext, aad=aad)
    assert cipher.decrypt(blob, aad=aad) == plaintext
