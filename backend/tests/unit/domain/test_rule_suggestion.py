"""RuleSuggestion and ToneSignal domain tests."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest

from svoi_pravila.domain.enums import (
    Firmness,
    RuleCategory,
    SuggestionSource,
    SuggestionStatus,
)
from svoi_pravila.domain.errors import InvalidTransitionError, InvalidValueError
from svoi_pravila.domain.ids import ContactId, RuleSuggestionId, UserId
from svoi_pravila.domain.rule_suggestion import (
    TONE_SIGNAL_WINDOW,
    RuleSuggestion,
    ToneSignal,
    dominant_firmness,
)
from svoi_pravila.domain.text import RuleText

NOW = datetime(2026, 1, 1, tzinfo=UTC)
USER = UserId(UUID(int=1))
CONTACT = ContactId(UUID(int=2))
SUGGESTION_ID = RuleSuggestionId(UUID(int=3))


def _tone(
    *,
    firmness: Firmness = Firmness.GENTLE,
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
        firmness=firmness,
        status=status,
        created_at=NOW,
        decided_at=decided_at,
    )


@pytest.mark.unit
def test_tone_suggestion_requires_firmness() -> None:
    with pytest.raises(InvalidValueError, match="tone suggestion requires firmness"):
        RuleSuggestion(
            id=SUGGESTION_ID,
            user_id=USER,
            contact_id=CONTACT,
            source=SuggestionSource.TONE,
            category=RuleCategory.HOW_TO_ASK,
            text=RuleText("x"),
            firmness=None,
            status=SuggestionStatus.PENDING,
            created_at=NOW,
            decided_at=None,
        )


@pytest.mark.unit
def test_decode_suggestion_forbids_firmness() -> None:
    with pytest.raises(InvalidValueError, match="decode suggestion forbids firmness"):
        RuleSuggestion(
            id=SUGGESTION_ID,
            user_id=USER,
            contact_id=CONTACT,
            source=SuggestionSource.DECODE,
            category=RuleCategory.HOW_TO_ASK,
            text=RuleText("x"),
            firmness=Firmness.FIRM,
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
        firmness=Firmness.GENTLE,
        now=NOW,
    )
    assert suggestion.status is SuggestionStatus.PENDING
    assert suggestion.firmness is Firmness.GENTLE
    assert suggestion.decided_at is None


@pytest.mark.unit
def test_create_decode_is_pending_without_firmness() -> None:
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
    assert suggestion.firmness is None
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
            firmness=Firmness.GENTLE,
            status=SuggestionStatus.ACCEPTED,
            created_at=NOW,
            decided_at=None,
        )


@pytest.mark.unit
def test_tone_signal_append_and_window() -> None:
    signal = ToneSignal(user_id=USER, contact_id=CONTACT, values=())
    for _ in range(TONE_SIGNAL_WINDOW + 3):
        signal = signal.append(Firmness.GENTLE)
    assert len(signal.values) == TONE_SIGNAL_WINDOW
    assert all(v is Firmness.GENTLE for v in signal.values)


@pytest.mark.unit
def test_tone_signal_rejects_oversize_construction() -> None:
    with pytest.raises(InvalidValueError, match="at most"):
        ToneSignal(
            user_id=USER,
            contact_id=CONTACT,
            values=tuple(Firmness.GENTLE for _ in range(TONE_SIGNAL_WINDOW + 1)),
        )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("values", "expected"),
    [
        ((), None),
        ((Firmness.GENTLE,) * 4, None),
        ((Firmness.GENTLE,) * 5, Firmness.GENTLE),
        ((Firmness.BALANCED,) * 4 + (Firmness.FIRM,), Firmness.BALANCED),
        ((Firmness.GENTLE,) * 4 + (Firmness.FIRM,), Firmness.GENTLE),
        ((Firmness.FIRM,) * 8 + (Firmness.GENTLE,) * 2, Firmness.FIRM),
        ((Firmness.GENTLE,) * 5 + (Firmness.BALANCED,) * 5, None),
        (
            (Firmness.GENTLE, Firmness.BALANCED, Firmness.FIRM, Firmness.GENTLE, Firmness.BALANCED),
            None,
        ),
        ((Firmness.GENTLE,) * 3 + (Firmness.BALANCED,) * 2, None),
        ((Firmness.GENTLE,) * 7 + (Firmness.FIRM,) * 3, None),
    ],
)
def test_dominant_firmness_table(
    values: tuple[Firmness, ...],
    expected: Firmness | None,
) -> None:
    signal = ToneSignal(user_id=USER, contact_id=CONTACT, values=values)
    assert dominant_firmness(signal) is expected
