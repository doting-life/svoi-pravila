"""Repository ports."""

from __future__ import annotations

from typing import Protocol

from svoi_pravila.domain.consent import Consent
from svoi_pravila.domain.contact import Contact
from svoi_pravila.domain.enums import Firmness, SuggestionSource
from svoi_pravila.domain.ids import (
    ContactId,
    InviteId,
    PairId,
    RuleId,
    RuleSuggestionId,
    TelegramUserId,
    UsageEventId,
    UserId,
)
from svoi_pravila.domain.invite import Invite, InviteTokenHash
from svoi_pravila.domain.pair import Pair
from svoi_pravila.domain.rule_suggestion import RuleSuggestion, ToneSignal
from svoi_pravila.domain.rules import Rule, RuleScope
from svoi_pravila.domain.usage import UsageEvent
from svoi_pravila.domain.user import User


class UserRepository(Protocol):
    """Persistence for users."""

    async def get(self, user_id: UserId) -> User | None:
        """Return user by id or None."""
        ...

    async def get_by_telegram_id(self, telegram_user_id: TelegramUserId) -> User | None:
        """Return user by Telegram id or None."""
        ...

    async def add(self, user: User) -> None:
        """Insert a new user.

        Raises ConflictError when ``telegram_user_id`` already exists.
        """
        ...

    async def update(self, user: User) -> None:
        """Persist an updated user."""
        ...

    async def delete(self, user_id: UserId) -> None:
        """Delete the user and shred the user DEK. No-op if missing."""
        ...


class ConsentRepository(Protocol):
    """Persistence for consents."""

    async def list_for_user(self, user_id: UserId) -> list[Consent]:
        """List all consent records for a user."""
        ...

    async def add(self, consent: Consent) -> None:
        """Insert a new consent."""
        ...

    async def update(self, consent: Consent) -> None:
        """Persist an updated consent."""
        ...

    async def delete_for_user(self, user_id: UserId) -> None:
        """Delete every consent row for the user."""
        ...


class ContactRepository(Protocol):
    """Persistence for contacts."""

    async def get(self, contact_id: ContactId) -> Contact | None:
        """Return contact by id or None."""
        ...

    async def list_for_owner(self, owner_id: UserId) -> list[Contact]:
        """List contacts owned by a user."""
        ...

    async def count_for_owner(self, owner_id: UserId) -> int:
        """Count contacts owned by a user."""
        ...

    async def add(self, contact: Contact) -> None:
        """Insert a new contact."""
        ...

    async def update(self, contact: Contact) -> None:
        """Persist an updated contact."""
        ...

    async def get_for_owner_and_pair(self, owner_id: UserId, pair_id: PairId) -> Contact | None:
        """Return the owner's contact linked to ``pair_id``, if any."""
        ...

    async def delete(self, contact_id: ContactId) -> None:
        """Delete a contact. No-op if missing."""
        ...


class PairRepository(Protocol):
    """Persistence for pairs."""

    async def get(self, pair_id: PairId) -> Pair | None:
        """Return pair by id or None."""
        ...

    async def find_between(self, a: UserId, b: UserId) -> Pair | None:
        """Return the pair containing both users, if any."""
        ...

    async def add(self, pair: Pair) -> None:
        """Insert a new pair.

        Raises ConflictError when a pair with the same two members exists.
        """
        ...

    async def list_for_member(self, user_id: UserId) -> list[Pair]:
        """List pairs that include ``user_id``."""
        ...

    async def delete(self, pair_id: PairId) -> None:
        """Delete the pair and shred the pair DEK. No-op if missing."""
        ...


class RuleRepository(Protocol):
    """Persistence for rules."""

    async def get(self, rule_id: RuleId) -> Rule | None:
        """Return rule by id or None."""
        ...

    async def list_for_scope(self, scope: RuleScope) -> list[Rule]:
        """List all rules for a scope."""
        ...

    async def count_open_for_scope(self, scope: RuleScope) -> int:
        """Count PROPOSED + ACTIVE rules for a scope."""
        ...

    async def add(self, rule: Rule) -> None:
        """Insert a new rule."""
        ...

    async def update(self, rule: Rule) -> None:
        """Persist an updated rule."""
        ...

    async def delete(self, rule_id: RuleId) -> None:
        """Delete a rule and its revisions. No-op if missing."""
        ...


class InviteRepository(Protocol):
    """Persistence for invites."""

    async def get(self, invite_id: InviteId) -> Invite | None:
        """Return invite by id or None."""
        ...

    async def get_by_token_hash(self, token_hash: InviteTokenHash) -> Invite | None:
        """Return invite by token hash or None."""
        ...

    async def add(self, invite: Invite) -> None:
        """Insert a new invite.

        Raises ConflictError when ``token_hash`` already exists.
        """
        ...

    async def update(self, invite: Invite) -> None:
        """Persist an updated invite."""
        ...

    async def list_involving(self, user_id: UserId) -> list[Invite]:
        """List invites created by or accepted by ``user_id``."""
        ...

    async def delete(self, invite_id: InviteId) -> None:
        """Delete an invite. No-op if missing."""
        ...


class UsageEventRepository(Protocol):
    """Persistence for C0 usage events."""

    async def add(self, event: UsageEvent) -> None:
        """Insert a usage event."""
        ...

    async def get(self, event_id: UsageEventId) -> UsageEvent | None:
        """Return the event by id or None."""
        ...

    async def delete_for_pseudonym(self, user_pseudonym: str) -> None:
        """Delete usage events keyed by analytics HMAC hex, not a Telegram id."""
        ...


class RuleSuggestionRepository(Protocol):
    """Persistence for rule suggestions."""

    async def get(self, suggestion_id: RuleSuggestionId) -> RuleSuggestion | None:
        """Return suggestion by id or None."""
        ...

    async def list_for_user(self, user_id: UserId) -> list[RuleSuggestion]:
        """List all suggestions owned by ``user_id``."""
        ...

    async def list_pending_for_contact(
        self,
        user_id: UserId,
        contact_id: ContactId,
    ) -> list[RuleSuggestion]:
        """List pending suggestions for ``(user_id, contact_id)``."""
        ...

    async def get_tone(
        self,
        user_id: UserId,
        contact_id: ContactId,
        firmness: Firmness,
    ) -> RuleSuggestion | None:
        """Return the tone suggestion for ``(user, contact, firmness)`` in any status."""
        ...

    async def has_pending_for_source(
        self,
        user_id: UserId,
        contact_id: ContactId,
        source: SuggestionSource,
    ) -> bool:
        """True when a pending suggestion already exists for ``(user, contact, source)``."""
        ...

    async def add(self, suggestion: RuleSuggestion) -> None:
        """Insert a new suggestion."""
        ...

    async def update(self, suggestion: RuleSuggestion) -> None:
        """Persist an updated suggestion (status transition)."""
        ...

    async def delete_for_user(self, user_id: UserId) -> None:
        """Delete every suggestion row for the user."""
        ...


class ToneSignalRepository(Protocol):
    """Persistence for tone signals."""

    async def get(
        self,
        user_id: UserId,
        contact_id: ContactId,
    ) -> ToneSignal | None:
        """Return the signal for ``(user_id, contact_id)`` or None (read-only)."""
        ...

    async def lock_for_append(
        self,
        user_id: UserId,
        contact_id: ContactId,
    ) -> ToneSignal:
        """Ensure a row exists, lock it (``FOR UPDATE``), and return the signal."""
        ...

    async def list_for_user(self, user_id: UserId) -> list[ToneSignal]:
        """List all tone signals for ``user_id``."""
        ...

    async def upsert(self, signal: ToneSignal) -> None:
        """Insert or replace the signal for ``(user_id, contact_id)``."""
        ...

    async def delete_for_user(self, user_id: UserId) -> None:
        """Delete every tone signal row for the user."""
        ...
