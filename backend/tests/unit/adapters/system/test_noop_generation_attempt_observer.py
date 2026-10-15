"""The production observer drops every invalid attempt."""

from __future__ import annotations

import pytest

from svoi_pravila.adapters.system.noop_generation_attempt_observer import (
    NoopGenerationAttemptObserver,
)
from svoi_pravila.application.errors import InvalidOutputReason
from svoi_pravila.application.ports.generation_attempt_observer import InvalidAttempt
from svoi_pravila.domain.enums import VariantKind


@pytest.mark.unit
def test_the_noop_observer_stores_nothing() -> None:
    observer = NoopGenerationAttemptObserver()
    attempt = InvalidAttempt(
        operation="rewrite",
        attempt=0,
        reason=InvalidOutputReason.PROFANITY,
        slot=VariantKind.FIRMER,
        texts=(("firmer", "CANARY-text"),),
    )
    observer.on_invalid_attempt(attempt)
    assert vars(observer) == {}
    assert "CANARY" not in repr(attempt)
