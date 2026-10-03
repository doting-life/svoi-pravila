"""Entity transition tests for User, Consent, Contact, Pair, Invite."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from uuid import UUID

import pytest

from svoi_pravila.domain.consent import Consent
from svoi_pravila.domain.contact import Contact
from svoi_pravila.domain.enums import ConsentKind, RelationshipKind
from svoi_pravila.domain.errors import (
    AlreadyLinkedError,
    InvalidTimestampError,
    InvalidTransitionError,
    InvalidValueError,
    InviteAlreadyAcceptedError,
    InviteExpiredError,
    NotAMemberError,
    SelfInviteAcceptError,
)
from svoi_pravila.domain.ids import ConsentId, ContactId, InviteId, PairId, TelegramUserId, UserId
from svoi_pravila.domain.invite import INVITE_TTL, Invite, InviteTokenHash
from svoi_pravila.domain.pair import Pair
from svoi_pravila.domain.text import ContactLabel, Sha256Hex
from svoi_pravila.domain.user import User

NOW = datetime(2026, 1, 1, tzinfo=UTC)
SHA = Sha256Hex("c" * 64)


@pytest.mark.unit
def test_user_confirm_age_idempotent() -> None:
    user = User(
        id=UserId(UUID(int=1)),
        telegram_user_id=TelegramUserId(10),
        created_at=NOW,
        age_confirmed_at=None,
        active_contact_id=None,
    )
    later = NOW + timedelta(hours=1)
    first = user.confirm_age(NOW)
    second = first.confirm_age(later)
    assert first.age_confirmed_at == NOW
    assert second.age_confirmed_at == NOW
    with pytest.raises(InvalidTransitionError):
        user.confirm_age(NOW - timedelta(seconds=1))
    with pytest.raises(InvalidValueError):
        User(
            id=UserId(UUID(int=2)),
            telegram_user_id=TelegramUserId(11),
            created_at=NOW,
            age_confirmed_at=NOW - timedelta(days=1),
            active_contact_id=None,
        )


@pytest.mark.unit
def test_rejects_naive_and_non_utc() -> None:
    with pytest.raises(InvalidTimestampError):
        User(
            id=UserId(UUID(int=1)),
            telegram_user_id=TelegramUserId(1),
            created_at=datetime(2026, 1, 1),
            age_confirmed_at=None,
            active_contact_id=None,
        )
    tz = timezone(timedelta(hours=3))
    with pytest.raises(InvalidTimestampError):
        User(
            id=UserId(UUID(int=1)),
            telegram_user_id=TelegramUserId(1),
            created_at=datetime(2026, 1, 1, tzinfo=tz),
            age_confirmed_at=None,
            active_contact_id=None,
        )


@pytest.mark.unit
def test_consent_revoke_and_validity() -> None:
    consent = Consent(
        id=ConsentId(UUID(int=1)),
        user_id=UserId(UUID(int=1)),
        kind=ConsentKind.PERSONAL_DATA,
        text_version="v1",
        text_sha256=SHA,
        granted_at=NOW,
        revoked_at=None,
    )
    assert consent.is_valid_for("v1", SHA.value)
    revoked = consent.revoke(NOW + timedelta(seconds=1))
    assert revoked.is_valid_for("v1", SHA.value) is False
    with pytest.raises(InvalidValueError):
        Consent(
            id=ConsentId(UUID(int=2)),
            user_id=UserId(UUID(int=1)),
            kind=ConsentKind.PERSONAL_DATA,
            text_version="v1",
            text_sha256=SHA,
            granted_at=NOW,
            revoked_at=NOW - timedelta(seconds=1),
        )
    with pytest.raises(InvalidValueError):
        Consent(
            id=ConsentId(UUID(int=3)),
            user_id=UserId(UUID(int=1)),
            kind=ConsentKind.PERSONAL_DATA,
            text_version="",
            text_sha256=SHA,
            granted_at=NOW,
            revoked_at=None,
        )
    with pytest.raises(InvalidTransitionError):
        revoked.revoke(NOW + timedelta(seconds=2))
    with pytest.raises(InvalidTransitionError):
        consent.revoke(NOW - timedelta(seconds=1))


@pytest.mark.unit
def test_contact_link_unlink() -> None:
    contact = Contact(
        id=ContactId(UUID(int=1)),
        owner_id=UserId(UUID(int=1)),
        label=ContactLabel("Alex"),
        relationship=RelationshipKind.FRIEND,
        pair_id=None,
        created_at=NOW,
    )
    pair_id = PairId(UUID(int=9))
    linked = contact.link_pair(pair_id)
    assert linked.pair_id == pair_id
    with pytest.raises(AlreadyLinkedError):
        linked.link_pair(PairId(UUID(int=10)))
    assert linked.unlink_pair().pair_id is None
    with pytest.raises(InvalidTransitionError):
        contact.unlink_pair()


@pytest.mark.unit
def test_pair_members() -> None:
    a, b = UserId(UUID(int=1)), UserId(UUID(int=2))
    pair = Pair(id=PairId(UUID(int=3)), members=frozenset({a, b}), created_at=NOW)
    assert pair.is_member(a)
    assert pair.other_member(a) == b
    with pytest.raises(NotAMemberError):
        pair.other_member(UserId(UUID(int=99)))
    with pytest.raises(InvalidValueError):
        Pair(id=PairId(UUID(int=4)), members=frozenset({a}), created_at=NOW)


@pytest.mark.unit
def test_invite_accept_and_invariants() -> None:
    inviter = UserId(UUID(int=1))
    invitee = UserId(UUID(int=2))
    invite = Invite.create(
        invite_id=InviteId(UUID(int=3)),
        inviter_id=inviter,
        contact_id=ContactId(UUID(int=4)),
        token_hash=InviteTokenHash.from_raw_token("tok"),
        created_at=NOW,
    )
    assert invite.expires_at == NOW + INVITE_TTL
    accepted = invite.accept(invitee, NOW + timedelta(days=1))
    assert accepted.accepted_by == invitee
    with pytest.raises(InviteAlreadyAcceptedError):
        accepted.accept(UserId(UUID(int=5)), NOW + timedelta(days=1))
    with pytest.raises(SelfInviteAcceptError):
        invite.accept(inviter, NOW)
    with pytest.raises(InviteExpiredError):
        invite.accept(invitee, NOW + INVITE_TTL)
    with pytest.raises(InvalidValueError):
        Invite(
            id=InviteId(UUID(int=6)),
            inviter_id=inviter,
            contact_id=ContactId(UUID(int=4)),
            token_hash=InviteTokenHash.from_raw_token("tok2"),
            created_at=NOW,
            expires_at=NOW + timedelta(days=1),
            accepted_by=None,
            accepted_at=None,
        )
    with pytest.raises(InvalidValueError):
        Invite(
            id=InviteId(UUID(int=7)),
            inviter_id=inviter,
            contact_id=ContactId(UUID(int=4)),
            token_hash=InviteTokenHash.from_raw_token("tok3"),
            created_at=NOW,
            expires_at=NOW + INVITE_TTL,
            accepted_by=invitee,
            accepted_at=None,
        )
    with pytest.raises(InvalidValueError):
        Invite(
            id=InviteId(UUID(int=8)),
            inviter_id=inviter,
            contact_id=ContactId(UUID(int=4)),
            token_hash=InviteTokenHash.from_raw_token("tok4"),
            created_at=NOW,
            expires_at=NOW + INVITE_TTL,
            accepted_by=invitee,
            accepted_at=NOW + INVITE_TTL,
        )
    with pytest.raises(InvalidValueError):
        Invite(
            id=InviteId(UUID(int=9)),
            inviter_id=inviter,
            contact_id=ContactId(UUID(int=4)),
            token_hash=InviteTokenHash.from_raw_token("tok5"),
            created_at=NOW,
            expires_at=NOW + INVITE_TTL,
            accepted_by=inviter,
            accepted_at=NOW + timedelta(hours=1),
        )


@pytest.mark.unit
def test_user_clear_active_contact() -> None:
    user = User(
        id=UserId(UUID(int=1)),
        telegram_user_id=TelegramUserId(10),
        created_at=NOW,
        age_confirmed_at=NOW,
        active_contact_id=ContactId(UUID(int=5)),
    )
    cleared = user.clear_active_contact()
    assert cleared.active_contact_id is None
    assert user.set_active_contact(ContactId(UUID(int=6))).active_contact_id == ContactId(
        UUID(int=6)
    )
