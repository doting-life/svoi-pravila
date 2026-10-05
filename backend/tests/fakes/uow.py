"""In-memory Unit of Work with commit/rollback semantics."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from types import TracebackType

from svoi_pravila.application.errors import ConflictError
from svoi_pravila.application.ports.repositories import (
    ConsentRepository,
    ContactRepository,
    InviteRepository,
    PairRepository,
    RuleRepository,
    RuleSuggestionRepository,
    ToneSignalRepository,
    UsageEventRepository,
    UserRepository,
)
from svoi_pravila.application.ports.unit_of_work import UnitOfWork
from svoi_pravila.domain.consent import Consent
from svoi_pravila.domain.contact import Contact
from svoi_pravila.domain.enums import Firmness, RuleStatus, SuggestionSource, SuggestionStatus
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


@dataclass
class _DurableState:
    """Committed durable state."""

    users: dict[UserId, User] = field(default_factory=dict)
    users_by_telegram: dict[int, UserId] = field(default_factory=dict)
    consents: dict[object, Consent] = field(default_factory=dict)
    contacts: dict[ContactId, Contact] = field(default_factory=dict)
    pairs: dict[PairId, Pair] = field(default_factory=dict)
    rules: dict[RuleId, Rule] = field(default_factory=dict)
    invites: dict[InviteId, Invite] = field(default_factory=dict)
    invites_by_hash: dict[str, InviteId] = field(default_factory=dict)
    usage_events: dict[UsageEventId, UsageEvent] = field(default_factory=dict)
    rule_suggestions: dict[RuleSuggestionId, RuleSuggestion] = field(default_factory=dict)
    tone_signals: dict[tuple[UserId, ContactId], ToneSignal] = field(default_factory=dict)


class InMemoryUserRepository:
    """Transactional user repository backed by a working copy."""

    def __init__(self, working: _DurableState) -> None:
        self._working = working

    async def get(self, user_id: UserId) -> User | None:
        return self._working.users.get(user_id)

    async def get_by_telegram_id(self, telegram_user_id: TelegramUserId) -> User | None:
        user_id = self._working.users_by_telegram.get(telegram_user_id.value)
        if user_id is None:
            return None
        return self._working.users.get(user_id)

    async def add(self, user: User) -> None:
        if user.telegram_user_id.value in self._working.users_by_telegram:
            raise ConflictError()
        self._working.users[user.id] = user
        self._working.users_by_telegram[user.telegram_user_id.value] = user.id

    async def update(self, user: User) -> None:
        self._working.users[user.id] = user
        self._working.users_by_telegram[user.telegram_user_id.value] = user.id

    async def delete(self, user_id: UserId) -> None:
        user = self._working.users.pop(user_id, None)
        if user is not None:
            self._working.users_by_telegram.pop(user.telegram_user_id.value, None)


class InMemoryConsentRepository:
    """Transactional consent repository."""

    def __init__(self, working: _DurableState) -> None:
        self._working = working

    async def list_for_user(self, user_id: UserId) -> list[Consent]:
        return [c for c in self._working.consents.values() if c.user_id == user_id]

    async def add(self, consent: Consent) -> None:
        self._working.consents[consent.id] = consent

    async def update(self, consent: Consent) -> None:
        self._working.consents[consent.id] = consent

    async def delete_for_user(self, user_id: UserId) -> None:
        to_drop = [
            cid for cid, consent in self._working.consents.items() if consent.user_id == user_id
        ]
        for cid in to_drop:
            del self._working.consents[cid]


class InMemoryContactRepository:
    """Transactional contact repository."""

    def __init__(self, working: _DurableState) -> None:
        self._working = working

    async def get(self, contact_id: ContactId) -> Contact | None:
        return self._working.contacts.get(contact_id)

    async def list_for_owner(self, owner_id: UserId) -> list[Contact]:
        return [c for c in self._working.contacts.values() if c.owner_id == owner_id]

    async def count_for_owner(self, owner_id: UserId) -> int:
        return sum(1 for c in self._working.contacts.values() if c.owner_id == owner_id)

    async def add(self, contact: Contact) -> None:
        self._working.contacts[contact.id] = contact

    async def update(self, contact: Contact) -> None:
        self._working.contacts[contact.id] = contact

    async def get_for_owner_and_pair(self, owner_id: UserId, pair_id: PairId) -> Contact | None:
        for contact in self._working.contacts.values():
            if contact.owner_id == owner_id and contact.pair_id == pair_id:
                return contact
        return None

    async def delete(self, contact_id: ContactId) -> None:
        self._working.contacts.pop(contact_id, None)


class InMemoryPairRepository:
    """Transactional pair repository."""

    def __init__(self, working: _DurableState) -> None:
        self._working = working

    async def get(self, pair_id: PairId) -> Pair | None:
        return self._working.pairs.get(pair_id)

    async def find_between(self, a: UserId, b: UserId) -> Pair | None:
        target = frozenset({a, b})
        for pair in self._working.pairs.values():
            if pair.members == target:
                return pair
        return None

    async def add(self, pair: Pair) -> None:
        if await self.find_between(*tuple(pair.members)) is not None:
            raise ConflictError()
        self._working.pairs[pair.id] = pair

    async def list_for_member(self, user_id: UserId) -> list[Pair]:
        return [pair for pair in self._working.pairs.values() if pair.is_member(user_id)]

    async def delete(self, pair_id: PairId) -> None:
        self._working.pairs.pop(pair_id, None)


class InMemoryRuleRepository:
    """Transactional rule repository."""

    def __init__(self, working: _DurableState) -> None:
        self._working = working

    async def get(self, rule_id: RuleId) -> Rule | None:
        return self._working.rules.get(rule_id)

    async def list_for_scope(self, scope: RuleScope) -> list[Rule]:
        return [r for r in self._working.rules.values() if r.scope == scope]

    async def count_open_for_scope(self, scope: RuleScope) -> int:
        return sum(
            1
            for r in self._working.rules.values()
            if r.scope == scope and r.status in {RuleStatus.PROPOSED, RuleStatus.ACTIVE}
        )

    async def add(self, rule: Rule) -> None:
        self._working.rules[rule.id] = rule

    async def update(self, rule: Rule) -> None:
        self._working.rules[rule.id] = rule

    async def delete(self, rule_id: RuleId) -> None:
        self._working.rules.pop(rule_id, None)


class InMemoryInviteRepository:
    """Transactional invite repository."""

    def __init__(self, working: _DurableState) -> None:
        self._working = working

    async def get(self, invite_id: InviteId) -> Invite | None:
        return self._working.invites.get(invite_id)

    async def get_by_token_hash(self, token_hash: InviteTokenHash) -> Invite | None:
        invite_id = self._working.invites_by_hash.get(token_hash.hex)
        if invite_id is None:
            return None
        return self._working.invites.get(invite_id)

    async def add(self, invite: Invite) -> None:
        if invite.token_hash.hex in self._working.invites_by_hash:
            raise ConflictError()
        self._working.invites[invite.id] = invite
        self._working.invites_by_hash[invite.token_hash.hex] = invite.id

    async def update(self, invite: Invite) -> None:
        self._working.invites[invite.id] = invite
        self._working.invites_by_hash[invite.token_hash.hex] = invite.id

    async def list_involving(self, user_id: UserId) -> list[Invite]:
        return [
            invite
            for invite in self._working.invites.values()
            if user_id in {invite.inviter_id, invite.accepted_by}
        ]

    async def delete(self, invite_id: InviteId) -> None:
        invite = self._working.invites.pop(invite_id, None)
        if invite is not None:
            self._working.invites_by_hash.pop(invite.token_hash.hex, None)


class InMemoryUsageEventRepository:
    """Transactional usage-event repository."""

    def __init__(self, working: _DurableState) -> None:
        self._working = working

    async def get(self, event_id: UsageEventId) -> UsageEvent | None:
        return self._working.usage_events.get(event_id)

    async def add(self, event: UsageEvent) -> None:
        self._working.usage_events[event.id] = event

    async def delete_for_pseudonym(self, user_pseudonym: str) -> None:
        to_drop = [
            eid
            for eid, event in self._working.usage_events.items()
            if event.user_pseudonym == user_pseudonym
        ]
        for eid in to_drop:
            del self._working.usage_events[eid]


class InMemoryRuleSuggestionRepository:
    """Transactional rule suggestion repository."""

    def __init__(self, working: _DurableState) -> None:
        self._working = working

    async def get(self, suggestion_id: RuleSuggestionId) -> RuleSuggestion | None:
        return self._working.rule_suggestions.get(suggestion_id)

    async def list_for_user(self, user_id: UserId) -> list[RuleSuggestion]:
        return [s for s in self._working.rule_suggestions.values() if s.user_id == user_id]

    async def list_pending_for_contact(
        self,
        user_id: UserId,
        contact_id: ContactId,
    ) -> list[RuleSuggestion]:
        return [
            s
            for s in self._working.rule_suggestions.values()
            if s.user_id == user_id
            and s.contact_id == contact_id
            and s.status is SuggestionStatus.PENDING
        ]

    async def get_tone(
        self,
        user_id: UserId,
        contact_id: ContactId,
        firmness: Firmness,
    ) -> RuleSuggestion | None:
        for suggestion in self._working.rule_suggestions.values():
            if (
                suggestion.user_id == user_id
                and suggestion.contact_id == contact_id
                and suggestion.source is SuggestionSource.TONE
                and suggestion.firmness is firmness
            ):
                return suggestion
        return None

    async def add(self, suggestion: RuleSuggestion) -> None:
        self._working.rule_suggestions[suggestion.id] = suggestion

    async def update(self, suggestion: RuleSuggestion) -> None:
        self._working.rule_suggestions[suggestion.id] = suggestion

    async def delete_for_user(self, user_id: UserId) -> None:
        to_drop = [
            sid
            for sid, suggestion in self._working.rule_suggestions.items()
            if suggestion.user_id == user_id
        ]
        for sid in to_drop:
            del self._working.rule_suggestions[sid]


class InMemoryToneSignalRepository:
    """Transactional tone signal repository."""

    def __init__(self, working: _DurableState) -> None:
        self._working = working

    async def get(self, user_id: UserId, contact_id: ContactId) -> ToneSignal | None:
        return self._working.tone_signals.get((user_id, contact_id))

    async def list_for_user(self, user_id: UserId) -> list[ToneSignal]:
        return [s for (uid, _), s in self._working.tone_signals.items() if uid == user_id]

    async def upsert(self, signal: ToneSignal) -> None:
        self._working.tone_signals[(signal.user_id, signal.contact_id)] = signal

    async def delete_for_user(self, user_id: UserId) -> None:
        to_drop = [key for key in self._working.tone_signals if key[0] == user_id]
        for key in to_drop:
            del self._working.tone_signals[key]


class InMemoryUnitOfWork:
    """Unit of work that commits working copies into shared durable state."""

    def __init__(self, durable: _DurableState) -> None:
        self._durable = durable
        self._working: _DurableState | None = None
        self._committed = False
        self.users: UserRepository
        self.consents: ConsentRepository
        self.contacts: ContactRepository
        self.pairs: PairRepository
        self.rules: RuleRepository
        self.invites: InviteRepository
        self.usage_events: UsageEventRepository
        self.rule_suggestions: RuleSuggestionRepository
        self.tone_signals: ToneSignalRepository

    async def __aenter__(self) -> InMemoryUnitOfWork:
        self._working = deepcopy(self._durable)
        self._committed = False
        self.users = InMemoryUserRepository(self._working)
        self.consents = InMemoryConsentRepository(self._working)
        self.contacts = InMemoryContactRepository(self._working)
        self.pairs = InMemoryPairRepository(self._working)
        self.rules = InMemoryRuleRepository(self._working)
        self.invites = InMemoryInviteRepository(self._working)
        self.usage_events = InMemoryUsageEventRepository(self._working)
        self.rule_suggestions = InMemoryRuleSuggestionRepository(self._working)
        self.tone_signals = InMemoryToneSignalRepository(self._working)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if not self._committed:
            self._working = None

    async def commit(self) -> None:
        if self._working is None:
            msg = "unit of work is not active"
            raise RuntimeError(msg)
        self._durable.users = self._working.users
        self._durable.users_by_telegram = self._working.users_by_telegram
        self._durable.consents = self._working.consents
        self._durable.contacts = self._working.contacts
        self._durable.pairs = self._working.pairs
        self._durable.rules = self._working.rules
        self._durable.invites = self._working.invites
        self._durable.invites_by_hash = self._working.invites_by_hash
        self._durable.usage_events = self._working.usage_events
        self._durable.rule_suggestions = self._working.rule_suggestions
        self._durable.tone_signals = self._working.tone_signals
        self._committed = True


class InMemoryUnitOfWorkFactory:
    """Factory returning units of work over shared durable state."""

    def __init__(self) -> None:
        self._durable = _DurableState()

    def __call__(self) -> UnitOfWork:
        return InMemoryUnitOfWork(self._durable)


__all__ = [
    "InMemoryUnitOfWork",
    "InMemoryUnitOfWorkFactory",
]
