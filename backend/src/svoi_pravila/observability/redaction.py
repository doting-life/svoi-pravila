"""Deep redaction of sensitive keys in structured log events.

Exception messages are never logged: library and framework exceptions can carry
DSNs, SQL parameters, and user-supplied values. The ``exc_value`` field from
structlog's exception renderer is redacted; ``exc_type`` and stack frames
(with locals already disabled) remain for diagnostics.
"""

from __future__ import annotations

from collections.abc import MutableMapping
from typing import Any

# Keys whose values must never appear in logs (normalized: lower-case, `-` → `_`).
# Covers conversation text, credentials, Telegram forward metadata, and exception
# messages (``exc_value``).
REDACTED_KEYS: frozenset[str] = frozenset(
    {
        "text",
        "message_text",
        "draft",
        "query",
        "prompt",
        "completion",
        "content",
        "body",
        "rule_text",
        "contact_label",
        "caption",
        "raw_update",
        "forward_origin",
        "password",
        "token",
        "secret",
        "authorization",
        "init_data",
        "exc_value",
    }
)

REDACTED_PLACEHOLDER = "[REDACTED]"


def _normalize_key(key: str) -> str:
    return key.lower().replace("-", "_")


def redact_value(value: object) -> object:
    """Return a copy of ``value`` with denylisted key values replaced."""
    if isinstance(value, dict):
        return redact_mapping(value)
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_value(item) for item in value)
    return value


def redact_mapping(data: dict[str, Any]) -> dict[str, Any]:
    """Redact a mapping; the structlog ``event`` key is never redacted."""
    result: dict[str, Any] = {}
    for key, value in data.items():
        if key == "event":
            result[key] = value
            continue
        if _normalize_key(str(key)) in REDACTED_KEYS:
            result[key] = REDACTED_PLACEHOLDER
            continue
        result[key] = redact_value(value)
    return result


def redact_log_processor(
    _logger: object,
    _method_name: str,
    event_dict: MutableMapping[str, Any],
) -> MutableMapping[str, Any]:
    """Structlog processor that redacts denylisted keys at any nesting depth."""
    return redact_mapping(dict(event_dict))
