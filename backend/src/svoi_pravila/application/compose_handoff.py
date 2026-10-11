"""Compose handoff sealed payload (inline → mini-app Compose)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from uuid import UUID

from svoi_pravila.domain.ids import ContactId

COMPOSE_HANDOFF_REF_PREFIX = "h_"
COMPOSE_HANDOFF_ID_LEN = 16
_HANDOFF_REF_BODY_LENGTH = 64
_HANDOFF_REF_RE = re.compile(
    rf"^{COMPOSE_HANDOFF_REF_PREFIX}[A-Za-z0-9_-]{{{_HANDOFF_REF_BODY_LENGTH}}}$"
)


@dataclass(frozen=True, slots=True)
class ComposeHandoffPayload:
    """Plaintext sealed behind a one-time handoff token."""

    draft: str
    contact_id: ContactId | None


def is_compose_handoff_ref(token: str) -> bool:
    """Return True when ``token`` matches the ``h_`` sealed reference form."""
    return _HANDOFF_REF_RE.fullmatch(token) is not None


def encode_compose_handoff_payload(payload: ComposeHandoffPayload) -> bytes:
    """Serialize payload (optional contact UUID + newline + draft)."""
    contact_raw = "" if payload.contact_id is None else str(payload.contact_id)
    return f"{contact_raw}\n{payload.draft}".encode()


def decode_compose_handoff_payload(plaintext: bytes) -> ComposeHandoffPayload:
    """Parse sealed plaintext; raises ``ValueError`` on defect."""
    try:
        decoded = plaintext.decode()
    except UnicodeDecodeError as exc:
        raise ValueError from exc
    contact_raw, separator, draft = decoded.partition("\n")
    if not separator:
        raise ValueError
    contact_id: ContactId | None = None
    if contact_raw:
        try:
            contact_id = ContactId(UUID(contact_raw))
        except ValueError as exc:
            raise ValueError from exc
    return ComposeHandoffPayload(draft=draft, contact_id=contact_id)
