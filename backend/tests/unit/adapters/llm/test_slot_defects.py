"""Slot defects of a rewrite answer: one pure function, all defects in slot order."""

from __future__ import annotations

import pytest

from svoi_pravila.adapters.llm.gigachat.validation import (
    MAX_VARIANT_CHARS,
    AllEmptyAnswerError,
    SlotDefect,
    SlotDefectsError,
    SlotValidation,
    is_all_empty_answer,
    rewrite_slot_defects,
    validate_rewrite_slots,
)
from svoi_pravila.application.errors import InvalidGenerationOutput, InvalidOutputReason
from svoi_pravila.application.ports.generation import TokenUsage
from svoi_pravila.domain.enums import VariantKind

_TOO_LONG = "x" * (MAX_VARIANT_CHARS + 1)


def _spec() -> SlotValidation:
    return SlotValidation(
        applied=[],
        safety_raw="ok",
        rule_count=0,
        usage=TokenUsage(),
        attempts=1,
        model="m",
        prompt_version="p",
        operation="rewrite",
    )


@pytest.mark.unit
def test_both_empty_slots_are_two_defects_in_slot_order() -> None:
    assert rewrite_slot_defects("c", "", "") == (
        SlotDefect(InvalidOutputReason.EMPTY_TEXT, VariantKind.SOFTER),
        SlotDefect(InvalidOutputReason.EMPTY_TEXT, VariantKind.FIRMER),
    )


@pytest.mark.unit
def test_a_whitespace_only_slot_is_empty() -> None:
    assert rewrite_slot_defects("c", "  \n", "ok text") == (
        SlotDefect(InvalidOutputReason.EMPTY_TEXT, VariantKind.SOFTER),
    )


@pytest.mark.unit
def test_one_empty_and_one_too_long_slot_are_two_defects() -> None:
    assert rewrite_slot_defects("c", "", _TOO_LONG) == (
        SlotDefect(InvalidOutputReason.EMPTY_TEXT, VariantKind.SOFTER),
        SlotDefect(InvalidOutputReason.TEXT_TOO_LONG, VariantKind.FIRMER),
    )


@pytest.mark.unit
def test_the_check_slot_is_checked_too_but_an_empty_check_is_not_a_defect() -> None:
    assert rewrite_slot_defects("", "soft", "firm") == ()
    assert rewrite_slot_defects("see https://example.test", "", "firm") == (
        SlotDefect(InvalidOutputReason.URL_IN_TEXT, VariantKind.CHECK),
        SlotDefect(InvalidOutputReason.EMPTY_TEXT, VariantKind.SOFTER),
    )


@pytest.mark.unit
def test_one_empty_slot_is_one_defect() -> None:
    assert rewrite_slot_defects("c", "soft", "") == (
        SlotDefect(InvalidOutputReason.EMPTY_TEXT, VariantKind.FIRMER),
    )


@pytest.mark.unit
def test_variant_kind_set_is_reported_only_when_no_slot_has_a_text_defect() -> None:
    assert rewrite_slot_defects("c", "same", "same") == (
        SlotDefect(InvalidOutputReason.VARIANT_KIND_SET),
    )
    assert rewrite_slot_defects("c", "", "") == (
        SlotDefect(InvalidOutputReason.EMPTY_TEXT, VariantKind.SOFTER),
        SlotDefect(InvalidOutputReason.EMPTY_TEXT, VariantKind.FIRMER),
    )
    assert rewrite_slot_defects("see https://example.test", "same", "same") == (
        SlotDefect(InvalidOutputReason.URL_IN_TEXT, VariantKind.CHECK),
    )
    assert rewrite_slot_defects("c", "soft", "firm") == ()


@pytest.mark.unit
def test_validation_raises_on_the_same_defects_with_the_first_one_for_the_application() -> None:
    with pytest.raises(SlotDefectsError) as exc_info:
        validate_rewrite_slots("c", "", _TOO_LONG, _spec())
    error = exc_info.value
    assert error.defects == rewrite_slot_defects("c", "", _TOO_LONG)
    assert error.reasons == (InvalidOutputReason.EMPTY_TEXT,)
    assert error.slots == (VariantKind.SOFTER,)
    assert error.labelled_reasons() == ("empty_text:softer",)
    assert isinstance(error, InvalidGenerationOutput)


@pytest.mark.unit
def test_validation_raises_variant_kind_set_without_a_slot() -> None:
    with pytest.raises(SlotDefectsError) as exc_info:
        validate_rewrite_slots("c", "same", "same", _spec())
    assert exc_info.value.labelled_reasons() == ("variant_kind_set",)


@pytest.mark.unit
def test_the_defect_signal_and_the_error_repr_hold_labels_only() -> None:
    with pytest.raises(SlotDefectsError) as exc_info:
        validate_rewrite_slots("CANARY-check https://x.test", "", "", _spec())
    error = exc_info.value
    assert "CANARY" not in repr(error)
    assert "CANARY" not in str(error)
    assert "CANARY" not in repr(error.defects)
    assert [defect.label for defect in error.defects] == [
        "url_in_text:check",
        "empty_text:softer",
        "empty_text:firmer",
    ]


@pytest.mark.unit
def test_an_answer_is_all_empty_only_when_every_slot_is_blank() -> None:
    assert is_all_empty_answer("", "", "")
    assert is_all_empty_answer(" ", "\n", "\t ")
    assert not is_all_empty_answer("c", "", "")
    assert not is_all_empty_answer("", "soft", "")
    assert not is_all_empty_answer("", "", "firm")


@pytest.mark.unit
def test_validation_marks_an_all_empty_answer_and_keeps_its_ordinary_defects() -> None:
    with pytest.raises(SlotDefectsError) as exc_info:
        validate_rewrite_slots("", " ", "", _spec())
    error = exc_info.value
    assert isinstance(error, AllEmptyAnswerError)
    assert error.all_empty is True
    assert [defect.label for defect in error.defects] == ["empty_text:softer", "empty_text:firmer"]
    assert error.reasons == (InvalidOutputReason.EMPTY_TEXT,)


@pytest.mark.unit
def test_validation_does_not_mark_an_answer_with_a_filled_slot_as_all_empty() -> None:
    with pytest.raises(SlotDefectsError) as exc_info:
        validate_rewrite_slots("c", "", "", _spec())
    assert not isinstance(exc_info.value, AllEmptyAnswerError)
    assert exc_info.value.all_empty is False
