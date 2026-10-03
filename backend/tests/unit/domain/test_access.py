"""Access policy tests."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest

from svoi_pravila.domain.access import (
    AccessRequirement,
    ConsentText,
    evaluate_access,
)
from svoi_pravila.domain.consent import Consent
from svoi_pravila.domain.enums import ConsentKind
from svoi_pravila.domain.errors import InvalidValueError
from svoi_pravila.domain.ids import ConsentId, TelegramUserId, UserId
from svoi_pravila.domain.text import Sha256Hex
from svoi_pravila.domain.user import User

NOW = datetime(2026, 1, 1, tzinfo=UTC)
SHA_A = Sha256Hex("a" * 64)
SHA_B = Sha256Hex("b" * 64)


def _user(*, age: bool) -> User:
    return User(
        id=UserId(UUID(int=1)),
        telegram_user_id=TelegramUserId(1),
        created_at=NOW,
        age_confirmed_at=NOW if age else None,
        active_contact_id=None,
    )


def _requirement() -> AccessRequirement:
    return AccessRequirement.from_kinds(
        {
            ConsentKind.PERSONAL_DATA: ConsentText("pd-v1", SHA_A),
            ConsentKind.SPECIAL_CATEGORY: ConsentText("sc-v1", SHA_B),
        }
    )


@pytest.mark.unit
def test_access_granted_when_age_and_consents_match() -> None:
    consents = [
        Consent(
            id=ConsentId(UUID(int=2)),
            user_id=UserId(UUID(int=1)),
            kind=ConsentKind.PERSONAL_DATA,
            text_version="pd-v1",
            text_sha256=SHA_A,
            granted_at=NOW,
            revoked_at=None,
        ),
        Consent(
            id=ConsentId(UUID(int=3)),
            user_id=UserId(UUID(int=1)),
            kind=ConsentKind.SPECIAL_CATEGORY,
            text_version="sc-v1",
            text_sha256=SHA_B,
            granted_at=NOW,
            revoked_at=None,
        ),
    ]
    status = evaluate_access(_user(age=True), consents, _requirement())
    assert status.granted is True
    assert status.missing_consents == frozenset()


@pytest.mark.unit
def test_outdated_consent_counts_as_missing() -> None:
    requirement = AccessRequirement.from_kinds(
        {
            ConsentKind.PERSONAL_DATA: ConsentText("pd-v2", SHA_A),
            ConsentKind.SPECIAL_CATEGORY: ConsentText("sc-v1", SHA_B),
        }
    )
    consents = [
        Consent(
            id=ConsentId(UUID(int=2)),
            user_id=UserId(UUID(int=1)),
            kind=ConsentKind.PERSONAL_DATA,
            text_version="pd-v1",
            text_sha256=SHA_A,
            granted_at=NOW,
            revoked_at=None,
        )
    ]
    status = evaluate_access(_user(age=True), consents, requirement)
    assert status.granted is False
    assert ConsentKind.PERSONAL_DATA in status.missing_consents


@pytest.mark.unit
def test_age_not_confirmed() -> None:
    status = evaluate_access(_user(age=False), [], _requirement())
    assert status.age_confirmed is False
    assert status.granted is False


@pytest.mark.unit
def test_access_requirement_must_cover_all_kinds() -> None:
    with pytest.raises(InvalidValueError):
        AccessRequirement.from_kinds({ConsentKind.PERSONAL_DATA: ConsentText("pd-v1", SHA_A)})


@pytest.mark.unit
def test_consent_text_version_must_be_non_empty() -> None:
    with pytest.raises(InvalidValueError):
        ConsentText("", SHA_A)


@pytest.mark.unit
def test_access_requirement_for_kind() -> None:
    requirement = _requirement()
    assert requirement.for_kind(ConsentKind.PERSONAL_DATA).version == "pd-v1"
    assert requirement.for_kind(ConsentKind.SPECIAL_CATEGORY).version == "sc-v1"
