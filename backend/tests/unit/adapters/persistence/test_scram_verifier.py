"""Unit tests for Postgres SCRAM-SHA-256 verifier construction."""

from __future__ import annotations

import pytest

from svoi_pravila.adapters.persistence.scram_verifier import (
    InvalidScramVerifierError,
    require_valid_scram_verifier,
    scram_sha256_verifier,
)

# Published vector for this codebase: password "secret", 16-byte salt
# 0123456789abcdef0123456789abcdef, 4096 iterations. Matches libpq
# PQencryptPasswordConn / RFC 5802 StoredKey+ServerKey construction.
_PASSWORD = "secret"
_SALT = bytes.fromhex("0123456789abcdef0123456789abcdef")
_EXPECTED = (
    "SCRAM-SHA-256$4096:ASNFZ4mrze8BI0VniavN7w==$"
    "NRSTL72+L5mWz7D5SWN9l+1UlmD+B5JVBleuxwyLstc=:"
    "dqIQeQpOHYtsQ1LJY6hNIpU4okBz35rLmKgd29fGzbA="
)


@pytest.mark.unit
def test_scram_sha256_verifier_known_vector() -> None:
    assert scram_sha256_verifier(_PASSWORD, salt=_SALT) == _EXPECTED


@pytest.mark.unit
def test_require_valid_scram_verifier_rejects_garbage() -> None:
    with pytest.raises(InvalidScramVerifierError):
        require_valid_scram_verifier("not-a-verifier")
