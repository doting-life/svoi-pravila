"""Shared envelope encryption primitives (AES-256-GCM) and pseudonyms."""

from __future__ import annotations

from svoi_pravila.crypto.cipher import FieldCipher, generate_dek, unwrap_dek, wrap_dek
from svoi_pravila.crypto.errors import DecryptionError
from svoi_pravila.crypto.pseudonymizer import HmacPseudonymizer

__all__ = [
    "DecryptionError",
    "FieldCipher",
    "HmacPseudonymizer",
    "generate_dek",
    "unwrap_dek",
    "wrap_dek",
]
