"""Repository ports."""

from __future__ import annotations

from typing import Protocol

from svoi_pravila.domain.consent import Consent
from svoi_pravila.domain.contact import Contact
from svoi_pravila.domain.ids import (
    ContactId,
    InviteId,
    PairId,
    RuleId,
    TelegramUserId,
    UsageEventId,
    UserId,
)
from svoi_pravila.domain.invite import Invite, InviteTokenHash
from svoi_pravila.domain.pair import Pair
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


class UsageEventRepository(Protocol):
    """Persistence for C0 usage events."""

    async def add(self, event: UsageEvent) -> None:
        """Insert a usage event."""
        ...

    async def get(self, event_id: UsageEventId) -> UsageEvent | None:
        """Return the event by id or None."""
        ...
