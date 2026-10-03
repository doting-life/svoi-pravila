"""Redaction processor tests."""

from __future__ import annotations

from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from svoi_pravila.observability.redaction import (
    REDACTED_KEYS,
    REDACTED_PLACEHOLDER,
    redact_log_processor,
    redact_mapping,
)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"event": "hello", "text": "secret"}, {"event": "hello", "text": REDACTED_PLACEHOLDER}),
        (
            {"event": "ok", "Authorization": "Bearer x"},
            {"event": "ok", "Authorization": REDACTED_PLACEHOLDER},
        ),
        (
            {"event": "ok", "nested": {"draft": "x", "count": 1}},
            {"event": "ok", "nested": {"draft": REDACTED_PLACEHOLDER, "count": 1}},
        ),
        (
            {"event": "ok", "items": [{"token": "a"}, {"id": 1}]},
            {"event": "ok", "items": [{"token": REDACTED_PLACEHOLDER}, {"id": 1}]},
        ),
        (
            {"event": "ok", "tuple_field": ({"password": "p"},)},
            {"event": "ok", "tuple_field": ({"password": REDACTED_PLACEHOLDER},)},
        ),
        (
            {"event": "message_text must stay", "safe": True},
            {"event": "message_text must stay", "safe": True},
        ),
        (
            {
                "event": "exc",
                "exception": [{"exc_type": "RuntimeError", "exc_value": "dsn://u:secret@h/db"}],
            },
            {
                "event": "exc",
                "exception": [
                    {"exc_type": "RuntimeError", "exc_value": REDACTED_PLACEHOLDER},
                ],
            },
        ),
    ],
)
def test_redaction_table(payload: dict[str, Any], expected: dict[str, Any]) -> None:
    assert redact_mapping(payload) == expected
    assert redact_log_processor(None, "info", payload) == expected


def _contains_denylisted_plaintext(value: object) -> bool:
    if isinstance(value, dict):
        for key, nested in value.items():
            if key == "event":
                continue
            normalized = str(key).lower().replace("-", "_")
            if normalized in REDACTED_KEYS and nested != REDACTED_PLACEHOLDER:
                return True
            if _contains_denylisted_plaintext(nested):
                return True
        return False
    if isinstance(value, list | tuple):
        return any(_contains_denylisted_plaintext(item) for item in value)
    return False


_jsonish = st.recursive(
    st.none() | st.booleans() | st.integers() | st.text(max_size=20),
    lambda children: (
        st.lists(children, max_size=3)
        | st.dictionaries(
            st.sampled_from([*sorted(REDACTED_KEYS), "safe", "id", "count", "Message-Text"]),
            children,
            max_size=4,
        )
    ),
    max_leaves=20,
)


@pytest.mark.unit
@given(payload=_jsonish)
def test_redaction_hypothesis_nested(payload: object) -> None:
    if not isinstance(payload, dict):
        payload = {"event": "probe", "data": payload}
    else:
        payload = {"event": "probe", **payload}
    redacted = redact_mapping(payload)
    assert redacted["event"] == "probe"
    assert not _contains_denylisted_plaintext(redacted)
