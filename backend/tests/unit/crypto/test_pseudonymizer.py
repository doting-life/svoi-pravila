"""HmacPseudonymizer property and unit tests."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from svoi_pravila.crypto import HmacPseudonymizer

PEPPER = b"\x33" * 32


@pytest.mark.unit
def test_deterministic_and_purpose_separated() -> None:
    p = HmacPseudonymizer(PEPPER)
    a = p.pseudonymize("rate_limit", "42")
    b = p.pseudonymize("rate_limit", "42")
    other = p.pseudonymize("analytics", "42")
    assert a == b
    assert a != other
    assert len(a) == 64
    int(a, 16)


@pytest.mark.unit
def test_rejects_weak_pepper_and_empty_purpose() -> None:
    with pytest.raises(ValueError):
        HmacPseudonymizer(b"short")
    with pytest.raises(ValueError):
        HmacPseudonymizer(PEPPER).pseudonymize("", "x")


@pytest.mark.unit
@given(
    purpose=st.text(min_size=1, max_size=32),
    value=st.text(max_size=64),
)
def test_property_hex_and_stable(purpose: str, value: str) -> None:
    p = HmacPseudonymizer(PEPPER)
    first = p.pseudonymize(purpose, value)
    second = p.pseudonymize(purpose, value)
    assert first == second
    assert len(first) == 64
    int(first, 16)
