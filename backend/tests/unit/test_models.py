"""Persistence metadata tests."""

from __future__ import annotations

import pytest

from svoi_pravila.adapters.persistence.models import NAMING_CONVENTION, Base


@pytest.mark.unit
def test_base_uses_naming_convention() -> None:
    assert Base.metadata.naming_convention == NAMING_CONVENTION
    assert "ix" in NAMING_CONVENTION
    assert "uq" in NAMING_CONVENTION
    assert "ck" in NAMING_CONVENTION
    assert "fk" in NAMING_CONVENTION
    assert "pk" in NAMING_CONVENTION
