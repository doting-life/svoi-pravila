"""Shared envelope encryption primitives (AES-256-GCM)."""

from __future__ import annotations

from svoi_pravila.crypto.cipher import FieldCipher, generate_dek, unwrap_dek, wrap_dek
from svoi_pravila.crypto.errors import DecryptionError

__all__ = [
    "DecryptionError",
    "FieldCipher",
    "generate_dek",
    "unwrap_dek",
    "wrap_dek",
]
