"""Rule-source sealed payload for mini-app «Сделать правилом»."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from svoi_pravila.domain.ids import ContactId

RULE_SOURCE_ID_LEN = 8


@dataclass(frozen=True, slots=True)
class RuleSourcePayload:
    """Plaintext sealed behind a one-time token."""

    contact_id: ContactId
    incoming_text: str


def encode_rule_source_payload(payload: RuleSourcePayload) -> bytes:
    """Serialize payload for sealed storage (contact UUID + newline + text)."""
    return f"{payload.contact_id}\n{payload.incoming_text}".encode()


def decode_rule_source_payload(plaintext: bytes) -> RuleSourcePayload:
    """Parse sealed plaintext into a payload; raises ``ValueError`` on defect."""
    try:
        decoded = plaintext.decode()
    except UnicodeDecodeError as exc:
        raise ValueError from exc
    contact_raw, separator, incoming = decoded.partition("\n")
    if not separator or not contact_raw:
        raise ValueError
    try:
        contact_id = ContactId(UUID(contact_raw))
    except ValueError as exc:
        raise ValueError from exc
    return RuleSourcePayload(contact_id=contact_id, incoming_text=incoming)
