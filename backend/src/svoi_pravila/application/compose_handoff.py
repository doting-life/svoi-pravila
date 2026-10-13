"""Compose handoff sealed payload (inline → mini-app Compose)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from uuid import UUID

from svoi_pravila.domain.ids import UserId

COMPOSE_HANDOFF_REF_PREFIX = "h_"
COMPOSE_HANDOFF_ID_LEN = 16
_HANDOFF_REF_BODY_LENGTH = 64
_HANDOFF_REF_RE = re.compile(
    rf"^{COMPOSE_HANDOFF_REF_PREFIX}[A-Za-z0-9_-]{{{_HANDOFF_REF_BODY_LENGTH}}}$"
)
_PAYLOAD_VERSION = "3"


@dataclass(frozen=True, slots=True)
class ComposeHandoffPayload:
    """Plaintext sealed behind a one-time bearer handoff token."""

    user_id: UserId
    draft: str


def is_compose_handoff_ref(token: str) -> bool:
    """Return True when ``token`` matches the ``h_`` sealed reference form."""
    return _HANDOFF_REF_RE.fullmatch(token) is not None


def encode_compose_handoff_payload(payload: ComposeHandoffPayload) -> bytes:
    """Serialize versioned payload (user id, draft)."""
    return f"{_PAYLOAD_VERSION}\n{payload.user_id}\n{payload.draft}".encode()


def decode_compose_handoff_payload(plaintext: bytes) -> ComposeHandoffPayload:
    """Parse sealed plaintext; raises ``ValueError`` on defect."""
    try:
        decoded = plaintext.decode()
    except UnicodeDecodeError as exc:
        raise ValueError from exc
    version, sep1, rest = decoded.partition("\n")
    if not sep1 or version != _PAYLOAD_VERSION:
        raise ValueError
    user_raw, sep2, draft = rest.partition("\n")
    if not sep2:
        raise ValueError
    try:
        user_id = UserId(UUID(user_raw))
    except ValueError as exc:
        raise ValueError from exc
    return ComposeHandoffPayload(user_id=user_id, draft=draft)
