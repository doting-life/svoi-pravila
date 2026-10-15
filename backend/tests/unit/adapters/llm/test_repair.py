"""Correction texts of the corrective retry (prompts/repair/v3.md) and the echo policy."""

from __future__ import annotations

import pytest

from svoi_pravila.adapters.llm.gigachat.estimate import (
    chars_to_tokens,
    estimate_prepared_tokens,
    max_structured_billable,
)
from svoi_pravila.adapters.llm.gigachat.prepared import PreparedMessages
from svoi_pravila.adapters.llm.gigachat.repair import (
    NO_ECHO,
    findings_correction,
    first_attempt_defect,
    formal_correction,
    longest_correction_chars,
    retry_messages,
)
from svoi_pravila.adapters.llm.gigachat.structured import _RetryAttemptError
from svoi_pravila.adapters.llm.gigachat.validation import SlotDefect
from svoi_pravila.application.errors import InvalidOutputReason
from svoi_pravila.domain.enums import VariantKind
from svoi_pravila.domain.lexicon_guard import FindingKind, SlotFinding

_GENERIC = (
    "Предыдущий ответ не прошёл проверку. Ответь заново строго по правилам и только JSON по схеме."
)
_KEEP = "Остальные поля верни без изменений. Ответь только JSON по схеме."
_EMPTY_SOFTER = (
    "Поле `softer` пустое. Заполни его по правилам; если после перевода грубых слов от фразы "
    "ничего не осталось, передай намерение автора спокойными словами."
)
_EMPTY_FIRMER = _EMPTY_SOFTER.replace("`softer`", "`firmer`")
_TOO_LONG_FIRMER = "Поле `firmer` слишком длинное. Сократи его по правилам."
_KIND_SET = "`softer` и `firmer` совпадают. Сделай их разными по словам по правилам."
_PREPARED = PreparedMessages(
    system="system text",
    user="user text",
    prompt_version="rewrite@v2",
    boundary_marker="SPBOUND_x",
    untrusted=("draft",),
)


def _defect(reason: InvalidOutputReason, slot: VariantKind | None = None) -> SlotDefect:
    return SlotDefect(reason, slot)


@pytest.mark.unit
def test_block_r3_lines_are_filled_with_the_slot_and_end_with_keep() -> None:
    assert formal_correction((_defect(InvalidOutputReason.EMPTY_TEXT, VariantKind.SOFTER),)) == (
        f"{_EMPTY_SOFTER}\n{_KEEP}"
    )
    assert formal_correction((_defect(InvalidOutputReason.TEXT_TOO_LONG, VariantKind.FIRMER),)) == (
        f"{_TOO_LONG_FIRMER}\n{_KEEP}"
    )
    assert formal_correction((_defect(InvalidOutputReason.VARIANT_KIND_SET),)) == (
        f"{_KIND_SET}\n{_KEEP}"
    )


@pytest.mark.unit
def test_every_defect_gets_its_line_in_slot_order_and_keep_comes_once() -> None:
    text = formal_correction(
        (
            _defect(InvalidOutputReason.EMPTY_TEXT, VariantKind.SOFTER),
            _defect(InvalidOutputReason.TEXT_TOO_LONG, VariantKind.FIRMER),
        )
    )
    assert text == f"{_EMPTY_SOFTER}\n{_TOO_LONG_FIRMER}\n{_KEEP}"
    both_empty = formal_correction(
        (
            _defect(InvalidOutputReason.EMPTY_TEXT, VariantKind.SOFTER),
            _defect(InvalidOutputReason.EMPTY_TEXT, VariantKind.FIRMER),
        )
    )
    assert both_empty == f"{_EMPTY_SOFTER}\n{_EMPTY_FIRMER}\n{_KEEP}"
    assert both_empty.count(_KEEP) == 1


@pytest.mark.unit
@pytest.mark.parametrize(
    "reason",
    [
        InvalidOutputReason.URL_IN_TEXT,
        InvalidOutputReason.MARKUP_FENCE,
        InvalidOutputReason.VARIANT_COUNT,
        InvalidOutputReason.PROMPT_LEAK,
        InvalidOutputReason.JSON_DECODE,
    ],
)
def test_reasons_without_their_own_line_use_generic_alone(reason: InvalidOutputReason) -> None:
    text = formal_correction((_defect(reason, VariantKind.SOFTER),))
    assert text == _GENERIC
    assert _KEEP not in text


@pytest.mark.unit
def test_one_defect_without_a_line_makes_the_whole_message_generic() -> None:
    text = formal_correction(
        (
            _defect(InvalidOutputReason.URL_IN_TEXT, VariantKind.CHECK),
            _defect(InvalidOutputReason.EMPTY_TEXT, VariantKind.SOFTER),
        )
    )
    assert text == _GENERIC


@pytest.mark.unit
def test_a_slot_line_without_a_slot_falls_back_to_generic() -> None:
    assert formal_correction((_defect(InvalidOutputReason.EMPTY_TEXT),)) == _GENERIC
    assert formal_correction((_defect(InvalidOutputReason.TEXT_TOO_LONG),)) == _GENERIC


@pytest.mark.unit
@pytest.mark.parametrize(
    "reason", [InvalidOutputReason.PROFANITY, InvalidOutputReason.GENERALIZATION]
)
def test_a_lexicon_reason_without_findings_falls_back_to_generic(
    reason: InvalidOutputReason,
) -> None:
    assert formal_correction((_defect(reason, VariantKind.SOFTER),)) == _GENERIC


@pytest.mark.unit
@pytest.mark.parametrize("reason", sorted(NO_ECHO))
def test_no_echo_reasons_drop_the_first_answer(reason: InvalidOutputReason) -> None:
    defect = first_attempt_defect(reason, None, '{"a": 1}')
    assert defect.answer is None
    assert [m["role"] for m in retry_messages(_PREPARED, defect)] == ["system", "user", "user"]


@pytest.mark.unit
def test_other_reasons_echo_the_first_answer_and_label_has_no_text() -> None:
    defect = first_attempt_defect(InvalidOutputReason.EMPTY_TEXT, VariantKind.SOFTER, '{"a": 1}')
    messages = retry_messages(_PREPARED, defect)
    assert [m["role"] for m in messages] == ["system", "user", "assistant", "user"]
    assert messages[2]["content"] == '{"a": 1}'
    assert messages[3]["content"] == f"{_EMPTY_SOFTER}\n{_KEEP}"
    assert defect.labels == ("empty_text:softer",)
    assert first_attempt_defect(InvalidOutputReason.JSON_DECODE, None, None).labels == (
        "json_decode",
    )


@pytest.mark.unit
def test_a_defect_with_several_slot_defects_labels_every_one_in_order() -> None:
    defects = (
        _defect(InvalidOutputReason.EMPTY_TEXT, VariantKind.SOFTER),
        _defect(InvalidOutputReason.EMPTY_TEXT, VariantKind.FIRMER),
    )
    defect = first_attempt_defect(
        InvalidOutputReason.EMPTY_TEXT, VariantKind.SOFTER, "{}", defects=defects
    )
    assert defect.labels == ("empty_text:softer", "empty_text:firmer")
    assert retry_messages(_PREPARED, defect)[3]["content"] == (
        f"{_EMPTY_SOFTER}\n{_EMPTY_FIRMER}\n{_KEEP}"
    )


@pytest.mark.unit
def test_an_unparsed_answer_is_never_echoed() -> None:
    defect = first_attempt_defect(InvalidOutputReason.URL_IN_TEXT, None, None)
    assert [m["role"] for m in retry_messages(_PREPARED, defect)] == ["system", "user", "user"]


@pytest.mark.unit
def test_max_structured_billable_counts_the_larger_second_request() -> None:
    cap = 500
    first = estimate_prepared_tokens("system text", "user text", output_cap=cap)
    correction = -(-longest_correction_chars() // 3)
    assert correction == chars_to_tokens("x" * longest_correction_chars())
    assert max_structured_billable(_PREPARED, output_cap=cap) == first + first + cap + correction
    assert max_structured_billable(_PREPARED, output_cap=cap) > 2 * first


@pytest.mark.unit
def test_repr_of_a_defect_and_of_the_retry_error_has_the_labels_but_never_the_answer() -> None:
    answer = '{"softer": "CANARY-answer-text"}'
    defects = (
        _defect(InvalidOutputReason.EMPTY_TEXT, VariantKind.SOFTER),
        _defect(InvalidOutputReason.EMPTY_TEXT, VariantKind.FIRMER),
    )
    defect = first_attempt_defect(
        InvalidOutputReason.EMPTY_TEXT, VariantKind.SOFTER, answer, defects=defects
    )
    assert defect.answer == answer
    assert defect.labels == ("empty_text:softer", "empty_text:firmer")
    assert "CANARY" not in repr(defect)
    assert "reason=" in repr(defect)
    assert repr(defects[0]) == repr(SlotDefect(InvalidOutputReason.EMPTY_TEXT, VariantKind.SOFTER))
    error = _RetryAttemptError(defect)
    assert "empty_text:softer,empty_text:firmer" in repr(error)
    assert "CANARY" not in repr(error)
    assert "CANARY" not in str(error)


def _finding(kind: FindingKind, slot: VariantKind, *words: str) -> SlotFinding:
    return SlotFinding(kind=kind, slot=slot, words=words)


@pytest.mark.unit
def test_a_findings_correction_has_one_line_per_finding_with_the_words_joined_then_keep() -> None:
    text = findings_correction(
        (
            _finding(FindingKind.PROFANITY, VariantKind.FIRMER, "один", "два"),
            _finding(FindingKind.GENERALIZATION, VariantKind.SOFTER, "ты всегда"),
        )
    )
    lines = text.split("\n")
    assert len(lines) == 3
    assert lines[0].startswith("В поле `firmer` остались грубые слова: один, два. ")
    assert lines[1].startswith("В поле `softer` есть обобщение: ты всегда. ")
    assert lines[2] == _KEEP
    assert text.count(_KEEP) == 1
    assert text.endswith(_KEEP)


@pytest.mark.unit
def test_words_that_look_like_placeholders_are_not_filled_again() -> None:
    text = findings_correction((_finding(FindingKind.PROFANITY, VariantKind.SOFTER, "{slot}"),))
    assert "грубые слова: {slot}." in text


@pytest.mark.unit
def test_retry_messages_send_the_findings_correction_after_the_echo() -> None:
    findings = (_finding(FindingKind.PROFANITY, VariantKind.FIRMER, "слово"),)
    defect = first_attempt_defect(InvalidOutputReason.PROFANITY, VariantKind.FIRMER, "{}", findings)
    messages = retry_messages(_PREPARED, defect)
    assert [m["role"] for m in messages] == ["system", "user", "assistant", "user"]
    assert messages[3]["content"] == findings_correction(findings)
    assert defect.labels == ("profanity:firmer",)


@pytest.mark.unit
def test_the_longest_correction_covers_the_widest_findings_message() -> None:
    words = tuple("я" * 40 for _ in range(5))
    findings = tuple(
        _finding(kind, slot, *words)
        for slot in (VariantKind.SOFTER, VariantKind.FIRMER)
        for kind in FindingKind
    )
    assert longest_correction_chars() >= len(findings_correction(findings))


@pytest.mark.unit
def test_the_longest_correction_covers_the_widest_formal_message() -> None:
    defects = (
        *(
            _defect(reason, slot)
            for slot in (VariantKind.CHECK, VariantKind.SOFTER, VariantKind.FIRMER)
            for reason in (InvalidOutputReason.EMPTY_TEXT, InvalidOutputReason.TEXT_TOO_LONG)
        ),
        _defect(InvalidOutputReason.VARIANT_KIND_SET),
    )
    assert longest_correction_chars() >= len(formal_correction(defects))
    assert longest_correction_chars() >= len(_GENERIC)


@pytest.mark.unit
def test_repr_of_a_defect_with_findings_never_contains_the_words() -> None:
    findings = (_finding(FindingKind.PROFANITY, VariantKind.FIRMER, "CANARY-word"),)
    defect = first_attempt_defect(InvalidOutputReason.PROFANITY, VariantKind.FIRMER, "{}", findings)
    error = _RetryAttemptError(defect)
    assert "CANARY" not in repr(defect)
    assert "CANARY" not in repr(error)
    assert "CANARY" not in str(error)
    assert "profanity:firmer" in str(error)
