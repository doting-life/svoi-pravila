"""fill_empty_check: an empty ``check`` slot means "no corrections"."""

from __future__ import annotations

import pytest

from svoi_pravila.application.ports.generation import Variant
from svoi_pravila.application.rewrite_variants import fill_empty_check
from svoi_pravila.domain.enums import VariantKind


def _variants(check: str, softer: str = "s", firmer: str = "f") -> tuple[Variant, ...]:
    return (
        Variant(text=check, kind=VariantKind.CHECK),
        Variant(text=softer, kind=VariantKind.SOFTER),
        Variant(text=firmer, kind=VariantKind.FIRMER),
    )


@pytest.mark.unit
def test_empty_check_is_replaced_with_the_draft_and_flagged() -> None:
    filled, was_empty = fill_empty_check("draft", _variants(""))
    assert was_empty is True
    assert filled == _variants("draft")


@pytest.mark.unit
def test_non_empty_check_and_other_empty_slots_are_left_alone() -> None:
    kept = _variants("fixed")
    assert fill_empty_check("draft", kept) == (kept, False)
    other = _variants("fixed", softer="")
    assert fill_empty_check("draft", other) == (other, False)


@pytest.mark.unit
def test_no_variants_stay_empty_and_unflagged() -> None:
    assert fill_empty_check("draft", ()) == ((), False)
