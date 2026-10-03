"""Crypto-layer errors (no ciphertext or plaintext in messages)."""

from __future__ import annotations


class DecryptionError(Exception):
    """Ciphertext could not be authenticated or has an unsupported version."""

    def __init__(self) -> None:
        super().__init__("decryption failed")
