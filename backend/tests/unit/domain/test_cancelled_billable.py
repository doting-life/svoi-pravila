"""Unit tests for cancelled-generation billable estimate."""

from __future__ import annotations

import math

import pytest

from svoi_pravila.domain.cancelled_billable import estimate_cancelled_billable


@pytest.mark.unit
def test_estimate_cancelled_billable_boundaries() -> None:
    assert estimate_cancelled_billable(0, 0) == 0
    assert estimate_cancelled_billable(0, 128) == 128
    assert estimate_cancelled_billable(1, 0) == 1
    assert estimate_cancelled_billable(2, 10) == 11
    assert estimate_cancelled_billable(3, 10) == 12
    assert estimate_cancelled_billable(100, 500) == math.ceil(100 / 2) + 500


@pytest.mark.unit
def test_estimate_cancelled_billable_rejects_negatives() -> None:
    with pytest.raises(ValueError, match="input_chars"):
        estimate_cancelled_billable(-1, 0)
    with pytest.raises(ValueError, match="max_output_tokens"):
        estimate_cancelled_billable(0, -1)
