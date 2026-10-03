"""AES-256-GCM field cipher and DEK wrapping."""

from __future__ import annotations

import os
import secrets
from uuid import UUID

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from svoi_pravila.crypto.errors import DecryptionError

_CIPHERTEXT_VERSION = 0x01
_NONCE_LEN = 12
_KEY_LEN = 32
_MIN_CIPHERTEXT_LEN = 1 + _NONCE_LEN + 16  # version + nonce + tag


class FieldCipher:
    """AES-256-GCM encryptor/decryptor with required AAD."""

    def __init__(self, key: bytes) -> None:
        if len(key) != _KEY_LEN:
            msg = f"key must be {_KEY_LEN} bytes"
            raise ValueError(msg)
        self._aesgcm = AESGCM(key)

    def encrypt(self, plaintext: bytes, *, aad: bytes) -> bytes:
        """Encrypt with a fresh 96-bit nonce; return versioned ciphertext."""
        nonce = os.urandom(_NONCE_LEN)
        ciphertext_and_tag = self._aesgcm.encrypt(nonce, plaintext, aad)
        return bytes((_CIPHERTEXT_VERSION,)) + nonce + ciphertext_and_tag

    def decrypt(self, blob: bytes, *, aad: bytes) -> bytes:
        """Decrypt a versioned ciphertext; raise DecryptionError on failure."""
        if len(blob) < _MIN_CIPHERTEXT_LEN:
            raise DecryptionError()
        if blob[0] != _CIPHERTEXT_VERSION:
            raise DecryptionError()
        nonce = blob[1 : 1 + _NONCE_LEN]
        ciphertext_and_tag = blob[1 + _NONCE_LEN :]
        try:
            return self._aesgcm.decrypt(nonce, ciphertext_and_tag, aad)
        except InvalidTag as exc:
            raise DecryptionError() from exc


def generate_dek() -> bytes:
    """Return a fresh 32-byte data-encryption key."""
    return secrets.token_bytes(_KEY_LEN)


def wrap_dek(kek: bytes, dek: bytes, *, owner_kind: str, owner_id: UUID) -> bytes:
    """Wrap a DEK under the KEK with owner-bound AAD."""
    aad = f"dek:{owner_kind}:{owner_id}".encode()
    return FieldCipher(kek).encrypt(dek, aad=aad)


def unwrap_dek(kek: bytes, wrapped: bytes, *, owner_kind: str, owner_id: UUID) -> bytes:
    """Unwrap a DEK; raise DecryptionError on failure."""
    aad = f"dek:{owner_kind}:{owner_id}".encode()
    return FieldCipher(kek).decrypt(wrapped, aad=aad)
