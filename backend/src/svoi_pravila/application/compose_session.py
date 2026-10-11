"""Compose session sealed payload (bearer; reusable within TTL)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from uuid import UUID

from svoi_pravila.domain.ids import ContactId, UserId

COMPOSE_SESSION_REF_PREFIX = "cs_"
COMPOSE_SESSION_ID_LEN = 16
COMPOSE_SESSION_TTL_SECONDS = 15 * 60
_SESSION_REF_BODY_LENGTH = 64
_SESSION_REF_RE = re.compile(
    rf"^{COMPOSE_SESSION_REF_PREFIX}[A-Za-z0-9_-]{{{_SESSION_REF_BODY_LENGTH}}}$"
)
_PAYLOAD_VERSION = "1"


@dataclass(frozen=True, slots=True)
class ComposeSessionPayload:
    """Plaintext sealed behind a reusable bearer compose session."""

    user_id: UserId
    contact_id: ContactId | None


def is_compose_session_ref(token: str) -> bool:
    """Return True when ``token`` matches the ``cs_`` sealed reference form."""
    return _SESSION_REF_RE.fullmatch(token) is not None


def encode_compose_session_payload(payload: ComposeSessionPayload) -> bytes:
    """Serialize versioned session payload."""
    contact_raw = "" if payload.contact_id is None else str(payload.contact_id)
    return f"{_PAYLOAD_VERSION}\n{payload.user_id}\n{contact_raw}".encode()


def decode_compose_session_payload(plaintext: bytes) -> ComposeSessionPayload:
    """Parse sealed plaintext; raises ``ValueError`` on defect."""
    try:
        decoded = plaintext.decode()
    except UnicodeDecodeError as exc:
        raise ValueError from exc
    version, sep1, rest = decoded.partition("\n")
    if not sep1 or version != _PAYLOAD_VERSION:
        raise ValueError
    user_raw, sep2, contact_raw = rest.partition("\n")
    if not sep2:
        raise ValueError
    try:
        user_id = UserId(UUID(user_raw))
    except ValueError as exc:
        raise ValueError from exc
    contact_id: ContactId | None = None
    if contact_raw:
        try:
            contact_id = ContactId(UUID(contact_raw))
        except ValueError as exc:
            raise ValueError from exc
    return ComposeSessionPayload(user_id=user_id, contact_id=contact_id)
