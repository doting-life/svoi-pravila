"""System adapter unit tests."""

from __future__ import annotations

from datetime import UTC

import pytest

from svoi_pravila.adapters.system import (
    SecretsInviteTokenGenerator,
    SystemClock,
    Uuid7IdGenerator,
)


@pytest.mark.unit
def test_system_clock_is_utc_aware() -> None:
    now = SystemClock().now()
    assert now.tzinfo is UTC


@pytest.mark.unit
def test_uuid7_is_version_7_and_monotonic() -> None:
    gen = Uuid7IdGenerator()
    first = gen.new_id()
    second = gen.new_id()
    assert first.version == 7
    assert second.version == 7
    assert first < second


@pytest.mark.unit
def test_invite_token_length_bound() -> None:
    token = SecretsInviteTokenGenerator().new_invite_token()
    assert 1 <= len(token) <= 60
