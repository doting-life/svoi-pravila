"""Rule-source sealed payload for the «Сделать правилом» decode button."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from svoi_pravila.domain.ids import ContactId

RULE_SOURCE_CALLBACK_PREFIX = "sn:"
RULE_SOURCE_ID_LEN = 8
# sn: (3) + urlsafe_b64(8+32).rstrip("=") (54) = 57 ≤ 64
RULE_SOURCE_CALLBACK_MAX_BYTES = 64


@dataclass(frozen=True, slots=True)
class RuleSourcePayload:
    """Plaintext sealed behind a one-time callback token."""

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


def rule_source_callback_data(token: str) -> str:
    """Build Telegram callback_data ``sn:{token}`` and assert the 64-byte limit."""
    data = f"{RULE_SOURCE_CALLBACK_PREFIX}{token}"
    if len(data.encode("utf-8")) > RULE_SOURCE_CALLBACK_MAX_BYTES:
        msg = "rule-source callback_data exceeds 64 bytes"
        raise ValueError(msg)
    return data
