"""Unit tests for generation port dataclasses."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from svoi_pravila.application.ports.generation import (
    DecodeRequest,
    HelpSayIntent,
    HelpSayRequest,
    RuleContext,
    SoftenRequest,
)
from svoi_pravila.domain.enums import RelationshipKind, RuleCategory


def _rule() -> RuleContext:
    return RuleContext(
        category=RuleCategory.OTHER,
        text="no sarcasm",
        effective_since=datetime(2026, 1, 1, tzinfo=UTC),
    )


@pytest.mark.unit
def test_soften_request_rejects_empty_draft() -> None:
    with pytest.raises(ValueError, match="draft"):
        SoftenRequest(
            draft="",
            rules=(),
            relationship=RelationshipKind.PARTNER,
            deadline_seconds=2.0,
        )


@pytest.mark.unit
def test_soften_request_rejects_oversized_draft() -> None:
    with pytest.raises(ValueError, match="draft"):
        SoftenRequest(
            draft="x" * 4001,
            rules=(_rule(),),
            relationship=RelationshipKind.FRIEND,
            deadline_seconds=2.0,
        )


@pytest.mark.unit
def test_help_say_and_decode_accept_bounded_text() -> None:
    help_req = HelpSayRequest(
        intent=HelpSayIntent.DECLINE,
        details="не сейчас",
        rules=(_rule(),),
        relationship=RelationshipKind.WORK,
        deadline_seconds=1.5,
    )
    decode_req = DecodeRequest(
        incoming="что это значит?",
        rules=(_rule(),),
        relationship=RelationshipKind.FAMILY,
        deadline_seconds=3.0,
    )
    assert help_req.details == "не сейчас"
    assert decode_req.incoming == "что это значит?"


@pytest.mark.unit
def test_decode_request_rejects_empty_incoming() -> None:
    with pytest.raises(ValueError, match="incoming"):
        DecodeRequest(
            incoming="",
            rules=(),
            relationship=RelationshipKind.OTHER,
            deadline_seconds=1.0,
        )
