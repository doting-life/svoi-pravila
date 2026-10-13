"""RuleSuggestion and ToneSignal domain tests."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest

from svoi_pravila.domain.enums import (
    RuleCategory,
    SuggestionSource,
    SuggestionStatus,
    VariantKind,
)
from svoi_pravila.domain.errors import InvalidTransitionError, InvalidValueError
from svoi_pravila.domain.ids import ContactId, RuleSuggestionId, UserId
from svoi_pravila.domain.rule_suggestion import (
    TONE_SIGNAL_WINDOW,
    RuleSuggestion,
    ToneSignal,
    dominant_variant_kind,
)
from svoi_pravila.domain.text import RuleText

NOW = datetime(2026, 1, 1, tzinfo=UTC)
USER = UserId(UUID(int=1))
CONTACT = ContactId(UUID(int=2))
SUGGESTION_ID = RuleSuggestionId(UUID(int=3))


def _tone(
    *,
    variant_kind: VariantKind | None = VariantKind.SOFTER,
    status: SuggestionStatus = SuggestionStatus.PENDING,
    decided_at: datetime | None = None,
) -> RuleSuggestion:
    return RuleSuggestion(
        id=SUGGESTION_ID,
        user_id=USER,
        contact_id=CONTACT,
        source=SuggestionSource.TONE,
        category=RuleCategory.HOW_TO_ASK,
        text=RuleText("Говорить мягко, без резких формулировок"),
        variant_kind=variant_kind,
        status=status,
        created_at=NOW,
        decided_at=decided_at,
    )


@pytest.mark.unit
@pytest.mark.parametrize("variant_kind", [None, VariantKind.CHECK])
def test_tone_suggestion_requires_softer_or_firmer(variant_kind: VariantKind | None) -> None:
    with pytest.raises(InvalidValueError, match="tone suggestion requires softer or firmer"):
        RuleSuggestion(
            id=SUGGESTION_ID,
            user_id=USER,
            contact_id=CONTACT,
            source=SuggestionSource.TONE,
            category=RuleCategory.HOW_TO_ASK,
            text=RuleText("x"),
            variant_kind=variant_kind,
            status=SuggestionStatus.PENDING,
            created_at=NOW,
            decided_at=None,
        )


@pytest.mark.unit
def test_decode_suggestion_forbids_variant_kind() -> None:
    with pytest.raises(InvalidValueError, match="decode suggestion forbids variant_kind"):
        RuleSuggestion(
            id=SUGGESTION_ID,
            user_id=USER,
            contact_id=CONTACT,
            source=SuggestionSource.DECODE,
            category=RuleCategory.HOW_TO_ASK,
            text=RuleText("x"),
            variant_kind=VariantKind.FIRMER,
            status=SuggestionStatus.PENDING,
            created_at=NOW,
            decided_at=None,
        )


@pytest.mark.unit
def test_create_tone_is_pending() -> None:
    suggestion = RuleSuggestion.create_tone(
        suggestion_id=SUGGESTION_ID,
        user_id=USER,
        contact_id=CONTACT,
        category=RuleCategory.HOW_TO_ASK,
        text=RuleText("Говорить мягко, без резких формулировок"),
        variant_kind=VariantKind.SOFTER,
        now=NOW,
    )
    assert suggestion.status is SuggestionStatus.PENDING
    assert suggestion.variant_kind is VariantKind.SOFTER
    assert suggestion.decided_at is None


@pytest.mark.unit
def test_create_decode_is_pending_without_variant_kind() -> None:
    suggestion = RuleSuggestion.create_decode(
        suggestion_id=SUGGESTION_ID,
        user_id=USER,
        contact_id=CONTACT,
        category=RuleCategory.HOW_TO_ASK,
        text=RuleText("Мы говорим спокойно и без резких формулировок"),
        now=NOW,
    )
    assert suggestion.status is SuggestionStatus.PENDING
    assert suggestion.source is SuggestionSource.DECODE
    assert suggestion.variant_kind is None
    assert suggestion.decided_at is None


@pytest.mark.unit
def test_accept_from_pending() -> None:
    accepted = _tone().accept(NOW)
    assert accepted.status is SuggestionStatus.ACCEPTED
    assert accepted.decided_at == NOW


@pytest.mark.unit
def test_dismiss_from_pending() -> None:
    dismissed = _tone().dismiss(NOW)
    assert dismissed.status is SuggestionStatus.DISMISSED
    assert dismissed.decided_at == NOW


@pytest.mark.unit
def test_accept_already_decided_raises() -> None:
    accepted = _tone().accept(NOW)
    with pytest.raises(InvalidTransitionError, match="already decided"):
        accepted.accept(NOW)


@pytest.mark.unit
def test_dismiss_already_decided_raises() -> None:
    dismissed = _tone().dismiss(NOW)
    with pytest.raises(InvalidTransitionError, match="already decided"):
        dismissed.dismiss(NOW)


@pytest.mark.unit
def test_pending_must_not_have_decided_at() -> None:
    with pytest.raises(InvalidValueError, match="pending suggestion has no decided_at"):
        _tone(decided_at=NOW)


@pytest.mark.unit
def test_decided_requires_decided_at() -> None:
    with pytest.raises(InvalidValueError, match="decided suggestion requires decided_at"):
        RuleSuggestion(
            id=SUGGESTION_ID,
            user_id=USER,
            contact_id=CONTACT,
            source=SuggestionSource.TONE,
            category=RuleCategory.HOW_TO_ASK,
            text=RuleText("x"),
            variant_kind=VariantKind.SOFTER,
            status=SuggestionStatus.ACCEPTED,
            created_at=NOW,
            decided_at=None,
        )


@pytest.mark.unit
def test_tone_signal_append_and_window() -> None:
    signal = ToneSignal(user_id=USER, contact_id=CONTACT, values=())
    for _ in range(TONE_SIGNAL_WINDOW + 3):
        signal = signal.append(VariantKind.SOFTER)
    assert len(signal.values) == TONE_SIGNAL_WINDOW
    assert all(v is VariantKind.SOFTER for v in signal.values)


@pytest.mark.unit
def test_tone_signal_rejects_check_choice() -> None:
    with pytest.raises(InvalidValueError, match="only softer or firmer"):
        ToneSignal(user_id=USER, contact_id=CONTACT, values=(VariantKind.CHECK,))


@pytest.mark.unit
def test_tone_signal_rejects_oversize_construction() -> None:
    with pytest.raises(InvalidValueError, match="at most"):
        ToneSignal(
            user_id=USER,
            contact_id=CONTACT,
            values=tuple(VariantKind.SOFTER for _ in range(TONE_SIGNAL_WINDOW + 1)),
        )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("values", "expected"),
    [
        ((), None),
        ((VariantKind.SOFTER,) * 4, None),
        ((VariantKind.SOFTER,) * 5, VariantKind.SOFTER),
        ((VariantKind.FIRMER,) * 4 + (VariantKind.SOFTER,), VariantKind.FIRMER),
        ((VariantKind.SOFTER,) * 4 + (VariantKind.FIRMER,), VariantKind.SOFTER),
        ((VariantKind.FIRMER,) * 8 + (VariantKind.SOFTER,) * 2, VariantKind.FIRMER),
        ((VariantKind.SOFTER,) * 5 + (VariantKind.FIRMER,) * 5, None),
        (
            (
                VariantKind.SOFTER,
                VariantKind.FIRMER,
                VariantKind.SOFTER,
                VariantKind.FIRMER,
                VariantKind.SOFTER,
            ),
            None,
        ),
        ((VariantKind.SOFTER,) * 3 + (VariantKind.FIRMER,) * 2, None),
        ((VariantKind.SOFTER,) * 7 + (VariantKind.FIRMER,) * 3, None),
    ],
)
def test_dominant_variant_kind_table(
    values: tuple[VariantKind, ...],
    expected: VariantKind | None,
) -> None:
    signal = ToneSignal(user_id=USER, contact_id=CONTACT, values=values)
    assert dominant_variant_kind(signal) is expected
