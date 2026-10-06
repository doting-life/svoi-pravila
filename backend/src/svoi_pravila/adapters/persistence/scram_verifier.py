"""Client-side PostgreSQL SCRAM-SHA-256 password verifiers (RFC 5802 / 7677).

Produces the same wire format as libpq ``PQencryptPasswordConn`` /
``psql \\password`` so ``ALTER ROLE … PASSWORD`` stores a verifier without the
server ever seeing the plaintext password.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets

_DEFAULT_ITERATIONS = 4096
_SALT_LEN = 16
_KEY_LEN = 32
_VERIFIER_RE = re.compile(
    r"^SCRAM-SHA-256\$4096:[A-Za-z0-9+/]+=*\$[A-Za-z0-9+/]+=*:[A-Za-z0-9+/]+=*$"
)


class InvalidScramVerifierError(ValueError):
    """Raised when a computed verifier fails the strict format check."""

    def __init__(self) -> None:
        super().__init__("SCRAM verifier failed format validation")


def scram_sha256_verifier(
    password: str,
    *,
    salt: bytes | None = None,
    iterations: int = _DEFAULT_ITERATIONS,
) -> str:
    """Return a Postgres SCRAM-SHA-256 verifier string for ``password``.

    Format: ``SCRAM-SHA-256$<iter>:<b64 salt>$<b64 StoredKey>:<b64 ServerKey>``.
    """
    if iterations != _DEFAULT_ITERATIONS:
        msg = f"iterations must be {_DEFAULT_ITERATIONS}"
        raise ValueError(msg)
    salt_bytes = secrets.token_bytes(_SALT_LEN) if salt is None else salt
    if len(salt_bytes) != _SALT_LEN:
        msg = f"salt must be {_SALT_LEN} bytes"
        raise ValueError(msg)
    salted_password = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt_bytes,
        iterations,
        dklen=_KEY_LEN,
    )
    client_key = hmac.new(salted_password, b"Client Key", hashlib.sha256).digest()
    stored_key = hashlib.sha256(client_key).digest()
    server_key = hmac.new(salted_password, b"Server Key", hashlib.sha256).digest()
    b64_salt = base64.b64encode(salt_bytes).decode("ascii")
    b64_stored = base64.b64encode(stored_key).decode("ascii")
    b64_server = base64.b64encode(server_key).decode("ascii")
    verifier = f"SCRAM-SHA-256${iterations}:{b64_salt}${b64_stored}:{b64_server}"
    return require_valid_scram_verifier(verifier)


def require_valid_scram_verifier(verifier: str) -> str:
    """Return ``verifier`` if it matches the strict SCRAM-SHA-256 storage format."""
    if _VERIFIER_RE.fullmatch(verifier) is None:
        raise InvalidScramVerifierError()
    return verifier
