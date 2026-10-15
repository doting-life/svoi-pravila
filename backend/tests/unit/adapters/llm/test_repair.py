"""Correction texts of the corrective retry (prompts/repair/v1.md) and the echo policy."""

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
    correction_text,
    first_attempt_defect,
    longest_correction_chars,
    retry_messages,
)
from svoi_pravila.application.errors import InvalidOutputReason
from svoi_pravila.domain.enums import VariantKind

_GENERIC = (
    "Предыдущий ответ не прошёл проверку. Ответь заново строго по правилам и только JSON по схеме."
)
_PREPARED = PreparedMessages(
    system="system text",
    user="user text",
    prompt_version="rewrite@v2",
    boundary_marker="SPBOUND_x",
    untrusted=("draft",),
)


@pytest.mark.unit
def test_block_p_lines_are_filled_with_the_slot() -> None:
    assert correction_text(InvalidOutputReason.EMPTY_TEXT, VariantKind.SOFTER) == (
        "Поле `softer` пустое. Заполни его по правилам; остальные поля верни без изменений. "
        "Ответь только JSON по схеме."
    )
    assert correction_text(InvalidOutputReason.TEXT_TOO_LONG, VariantKind.FIRMER) == (
        "Поле `firmer` слишком длинное. Сократи его по правилам; остальные поля верни без "
        "изменений. Ответь только JSON по схеме."
    )
    assert correction_text(InvalidOutputReason.VARIANT_KIND_SET, None) == (
        "`softer` и `firmer` совпадают. Сделай их разными по словам по правилам; "
        "остальные поля верни без изменений. Ответь только JSON по схеме."
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    "reason",
    [
        InvalidOutputReason.URL_IN_TEXT,
        InvalidOutputReason.VARIANT_COUNT,
        InvalidOutputReason.PROMPT_LEAK,
        InvalidOutputReason.JSON_DECODE,
    ],
)
def test_reasons_without_their_own_line_use_generic(reason: InvalidOutputReason) -> None:
    assert correction_text(reason, VariantKind.SOFTER) == _GENERIC


@pytest.mark.unit
def test_a_slot_line_without_a_slot_falls_back_to_generic() -> None:
    assert correction_text(InvalidOutputReason.EMPTY_TEXT, None) == _GENERIC
    assert correction_text(InvalidOutputReason.TEXT_TOO_LONG, None) == _GENERIC


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
    assert defect.label == "empty_text:softer"
    assert first_attempt_defect(InvalidOutputReason.JSON_DECODE, None, None).label == "json_decode"


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
