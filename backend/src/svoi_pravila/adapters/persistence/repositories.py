"""SQLAlchemy repository implementations with encrypting mappers."""

from __future__ import annotations

from datetime import date
from typing import cast
from uuid import UUID

from sqlalchemy import Select, delete, func, or_, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from svoi_pravila.adapters.persistence.aad import (
    contact_label_aad,
    rule_revision_text_aad,
    rule_suggestion_text_aad,
)
from svoi_pravila.adapters.persistence.errors import flush_or_raise
from svoi_pravila.adapters.persistence.key_ring import KeyRing
from svoi_pravila.adapters.persistence.models import (
    ConsentRow,
    ContactRow,
    InviteRow,
    PairRow,
    RuleRevisionRow,
    RuleRow,
    RuleSuggestionRow,
    ToneSignalRow,
    UsageEventRow,
    UserRow,
)
from svoi_pravila.adapters.persistence.registry import RowRegistry
from svoi_pravila.crypto import FieldCipher
from svoi_pravila.domain.consent import Consent
from svoi_pravila.domain.contact import Contact
from svoi_pravila.domain.enums import (
    ConsentKind,
    Firmness,
    LimitKind,
    RelationshipKind,
    RuleCategory,
    RuleStatus,
    SuggestionSource,
    SuggestionStatus,
    UsageEventKind,
    UsageOutcome,
    UsageScenario,
    UsageSurface,
)
from svoi_pravila.domain.ids import (
    ConsentId,
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
from svoi_pravila.domain.rules import (
    ContactScope,
    PairScope,
    Rule,
    RuleRevision,
    RuleScope,
)
from svoi_pravila.domain.text import ContactLabel, RuleText, Sha256Hex
from svoi_pravila.domain.usage import UsageEvent
from svoi_pravila.domain.user import User


class SqlAlchemyUserRepository:
    """User repository."""

    def __init__(self, session: AsyncSession, keys: KeyRing, registry: RowRegistry) -> None:
        self._session = session
        self._keys = keys
        self._registry = registry

    def _to_domain(self, row: UserRow) -> User:
        return User(
            id=UserId(row.id),
            telegram_user_id=TelegramUserId(row.telegram_user_id),
            created_at=row.created_at,
            age_confirmed_at=row.age_confirmed_at,
            active_contact_id=ContactId(row.active_contact_id)
            if row.active_contact_id is not None
            else None,
        )

    async def get(self, user_id: UserId) -> User | None:
        row = await self._session.get(UserRow, user_id)
        if row is None:
            return None
        self._registry.register(UserRow, row.id, row)
        return self._to_domain(row)

    async def get_by_telegram_id(self, telegram_user_id: TelegramUserId) -> User | None:
        result = await self._session.execute(
            select(UserRow).where(UserRow.telegram_user_id == telegram_user_id.value)
        )
        row = result.scalar_one_or_none()
        if row is None:
            return None
        self._registry.register(UserRow, row.id, row)
        return self._to_domain(row)

    async def add(self, user: User) -> None:
        row = UserRow(
            id=user.id,
            telegram_user_id=user.telegram_user_id.value,
            created_at=user.created_at,
            age_confirmed_at=user.age_confirmed_at,
            active_contact_id=user.active_contact_id,
            version=1,
        )
        self._session.add(row)
        self._registry.register(UserRow, user.id, row)
        # Flush user before user_keys so INSERT order respects the FK
        # (users ↔ contacts cycle can otherwise confuse the unit of work sorter).
        await flush_or_raise(self._session)
        await self._keys.create_user_dek(user.id, created_at=user.created_at)
        await flush_or_raise(self._session)

    async def update(self, user: User) -> None:
        row = self._registry.require(UserRow, user.id)
        row.telegram_user_id = user.telegram_user_id.value
        row.age_confirmed_at = user.age_confirmed_at
        row.active_contact_id = user.active_contact_id
        await flush_or_raise(self._session)

    async def delete(self, user_id: UserId) -> None:
        await self._keys.delete_user_dek(user_id)
        row = await self._session.get(UserRow, user_id)
        if row is not None:
            await self._session.delete(row)
        await flush_or_raise(self._session)


class SqlAlchemyConsentRepository:
    """Consent repository."""

    def __init__(self, session: AsyncSession, registry: RowRegistry) -> None:
        self._session = session
        self._registry = registry

    def _to_domain(self, row: ConsentRow) -> Consent:
        return Consent(
            id=ConsentId(row.id),
            user_id=UserId(row.user_id),
            kind=ConsentKind(row.kind),
            text_version=row.text_version,
            text_sha256=Sha256Hex(row.text_sha256),
            granted_at=row.granted_at,
            revoked_at=row.revoked_at,
        )

    async def list_for_user(self, user_id: UserId) -> list[Consent]:
        result = await self._session.execute(
            select(ConsentRow).where(ConsentRow.user_id == user_id)
        )
        consents: list[Consent] = []
        for row in result.scalars():
            self._registry.register(ConsentRow, row.id, row)
            consents.append(self._to_domain(row))
        return consents

    async def add(self, consent: Consent) -> None:
        row = ConsentRow(
            id=consent.id,
            user_id=consent.user_id,
            kind=consent.kind.value,
            text_version=consent.text_version,
            text_sha256=consent.text_sha256.value,
            granted_at=consent.granted_at,
            revoked_at=consent.revoked_at,
        )
        self._session.add(row)
        self._registry.register(ConsentRow, consent.id, row)
        await flush_or_raise(self._session)

    async def update(self, consent: Consent) -> None:
        row = self._registry.require(ConsentRow, consent.id)
        row.revoked_at = consent.revoked_at
        await flush_or_raise(self._session)

    async def delete_for_user(self, user_id: UserId) -> None:
        await self._session.execute(delete(ConsentRow).where(ConsentRow.user_id == user_id))
        await flush_or_raise(self._session)


class SqlAlchemyContactRepository:
    """Contact repository with label encryption."""

    def __init__(self, session: AsyncSession, keys: KeyRing, registry: RowRegistry) -> None:
        self._session = session
        self._keys = keys
        self._registry = registry

    async def _to_domain(self, row: ContactRow) -> Contact:
        dek = await self._keys.user_dek(row.owner_id)
        label = FieldCipher(dek).decrypt(row.label_ciphertext, aad=contact_label_aad(row.id))
        plaintext = label.decode("utf-8")
        self._registry.register(ContactRow, row.id, row)
        self._registry.register_contact_label(row.id, plaintext)
        return Contact(
            id=ContactId(row.id),
            owner_id=UserId(row.owner_id),
            label=ContactLabel(plaintext),
            relationship=RelationshipKind(row.relationship),
            pair_id=PairId(row.pair_id) if row.pair_id is not None else None,
            created_at=row.created_at,
        )

    async def _encrypt_label(self, contact: Contact) -> bytes:
        dek = await self._keys.user_dek(contact.owner_id)
        return FieldCipher(dek).encrypt(
            contact.label.value.encode("utf-8"),
            aad=contact_label_aad(contact.id),
        )

    async def get(self, contact_id: ContactId) -> Contact | None:
        row = await self._session.get(ContactRow, contact_id)
        if row is None:
            return None
        return await self._to_domain(row)

    async def list_for_owner(self, owner_id: UserId) -> list[Contact]:
        result = await self._session.execute(
            select(ContactRow).where(ContactRow.owner_id == owner_id)
        )
        return [await self._to_domain(row) for row in result.scalars()]

    async def count_for_owner(self, owner_id: UserId) -> int:
        result = await self._session.execute(
            select(func.count()).select_from(ContactRow).where(ContactRow.owner_id == owner_id)
        )
        return int(result.scalar_one())

    async def add(self, contact: Contact) -> None:
        ciphertext = await self._encrypt_label(contact)
        row = ContactRow(
            id=contact.id,
            owner_id=contact.owner_id,
            label_ciphertext=ciphertext,
            relationship=contact.relationship.value,
            pair_id=contact.pair_id,
            created_at=contact.created_at,
            version=1,
        )
        self._session.add(row)
        self._registry.register(ContactRow, contact.id, row)
        self._registry.register_contact_label(contact.id, contact.label.value)
        await flush_or_raise(self._session)

    async def update(self, contact: Contact) -> None:
        row = self._registry.require(ContactRow, contact.id)
        snapshot = self._registry.contact_label(contact.id)
        if snapshot is None or snapshot != contact.label.value:
            row.label_ciphertext = await self._encrypt_label(contact)
            self._registry.register_contact_label(contact.id, contact.label.value)
        row.relationship = contact.relationship.value
        row.pair_id = contact.pair_id
        await flush_or_raise(self._session)

    async def get_for_owner_and_pair(self, owner_id: UserId, pair_id: PairId) -> Contact | None:
        result = await self._session.execute(
            select(ContactRow).where(
                ContactRow.owner_id == owner_id,
                ContactRow.pair_id == pair_id,
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            return None
        return await self._to_domain(row)

    async def delete(self, contact_id: ContactId) -> None:
        row = await self._session.get(ContactRow, contact_id)
        if row is not None:
            await self._session.delete(row)
        await flush_or_raise(self._session)


class SqlAlchemyPairRepository:
    """Pair repository."""

    def __init__(self, session: AsyncSession, keys: KeyRing, registry: RowRegistry) -> None:
        self._session = session
        self._keys = keys
        self._registry = registry

    def _to_domain(self, row: PairRow) -> Pair:
        return Pair(
            id=PairId(row.id),
            members=frozenset({UserId(row.member_low), UserId(row.member_high)}),
            created_at=row.created_at,
        )

    async def get(self, pair_id: PairId) -> Pair | None:
        row = await self._session.get(PairRow, pair_id)
        if row is None:
            return None
        self._registry.register(PairRow, row.id, row)
        return self._to_domain(row)

    async def find_between(self, a: UserId, b: UserId) -> Pair | None:
        low, high = sorted((a, b))
        result = await self._session.execute(
            select(PairRow).where(
                PairRow.member_low == low,
                PairRow.member_high == high,
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            return None
        self._registry.register(PairRow, row.id, row)
        return self._to_domain(row)

    async def add(self, pair: Pair) -> None:
        low, high = sorted(pair.members)
        row = PairRow(
            id=pair.id,
            member_low=low,
            member_high=high,
            created_at=pair.created_at,
        )
        self._session.add(row)
        self._registry.register(PairRow, pair.id, row)
        await flush_or_raise(self._session)
        await self._keys.create_pair_dek(pair.id, created_at=pair.created_at)
        await flush_or_raise(self._session)

    async def list_for_member(self, user_id: UserId) -> list[Pair]:
        result = await self._session.execute(
            select(PairRow).where(
                or_(PairRow.member_low == user_id, PairRow.member_high == user_id)
            )
        )
        pairs: list[Pair] = []
        for row in result.scalars():
            self._registry.register(PairRow, row.id, row)
            pairs.append(self._to_domain(row))
        return pairs

    async def delete(self, pair_id: PairId) -> None:
        await self._keys.delete_pair_dek(pair_id)
        row = await self._session.get(PairRow, pair_id)
        if row is not None:
            await self._session.delete(row)
        await flush_or_raise(self._session)


class SqlAlchemyRuleRepository:
    """Rule repository with revision encryption and sync."""

    def __init__(self, session: AsyncSession, keys: KeyRing, registry: RowRegistry) -> None:
        self._session = session
        self._keys = keys
        self._registry = registry

    async def _scope_dek(self, scope: RuleScope) -> bytes:
        if isinstance(scope, ContactScope):
            contact = self._registry.get(ContactRow, scope.contact_id)
            if contact is None:
                contact = await self._session.get(ContactRow, scope.contact_id)
                if contact is None:
                    msg = "contact missing for rule scope"
                    raise RuntimeError(msg)
                self._registry.register(ContactRow, contact.id, contact)
            return await self._keys.user_dek(contact.owner_id)
        return await self._keys.pair_dek(scope.pair_id)

    async def _to_domain(self, row: RuleRow) -> Rule:
        if row.scope_contact_id is not None:
            scope: RuleScope = ContactScope(ContactId(row.scope_contact_id))
        elif row.scope_pair_id is not None:
            scope = PairScope(PairId(row.scope_pair_id))
        else:
            msg = "rule row missing scope"
            raise RuntimeError(msg)
        dek = await self._scope_dek(scope)
        cipher = FieldCipher(dek)
        revisions: list[RuleRevision] = []
        for rev in sorted(row.revisions, key=lambda r: r.number):
            plaintext = cipher.decrypt(
                rev.text_ciphertext,
                aad=rule_revision_text_aad(row.id, rev.number),
            )
            revisions.append(
                RuleRevision(
                    number=rev.number,
                    text=RuleText(plaintext.decode("utf-8")),
                    author_id=UserId(rev.author_id),
                    proposed_at=rev.proposed_at,
                    approved_by=frozenset(UserId(u) for u in rev.approved_by),
                    effective_since=rev.effective_since,
                )
            )
        self._registry.register(RuleRow, row.id, row)
        return Rule(
            id=RuleId(row.id),
            scope=scope,
            category=RuleCategory(row.category),
            approvers=frozenset(UserId(u) for u in row.approver_ids),
            status=RuleStatus(row.status),
            revisions=tuple(revisions),
            created_at=row.created_at,
        )

    async def get(self, rule_id: RuleId) -> Rule | None:
        row = await self._session.get(RuleRow, rule_id)
        if row is None:
            return None
        return await self._to_domain(row)

    def _scope_filter(self, scope: RuleScope) -> Select[RuleRow]:
        if isinstance(scope, ContactScope):
            return select(RuleRow).where(RuleRow.scope_contact_id == scope.contact_id)
        return select(RuleRow).where(RuleRow.scope_pair_id == scope.pair_id)

    async def list_for_scope(self, scope: RuleScope) -> list[Rule]:
        result = await self._session.execute(self._scope_filter(scope))
        return [await self._to_domain(row) for row in result.scalars().all()]

    async def count_open_for_scope(self, scope: RuleScope) -> int:
        stmt = self._scope_filter(scope).where(
            RuleRow.status.in_([RuleStatus.PROPOSED.value, RuleStatus.ACTIVE.value])
        )
        result = await self._session.execute(select(func.count()).select_from(stmt.subquery()))
        return int(result.scalar_one())

    async def _write_revisions(self, row: RuleRow, rule: Rule, dek: bytes) -> None:
        cipher = FieldCipher(dek)
        existing = {rev.number: rev for rev in row.revisions}
        wanted_numbers = {rev.number for rev in rule.revisions}
        for number, rev_row in list(existing.items()):
            if number not in wanted_numbers:
                await self._session.delete(rev_row)
                row.revisions.remove(rev_row)
        for rev in rule.revisions:
            approved = cast(list[UUID], sorted(rev.approved_by))
            if rev.number in existing:
                rev_row = existing[rev.number]
                rev_row.approved_by = approved
                rev_row.effective_since = rev.effective_since
            else:
                ciphertext = cipher.encrypt(
                    rev.text.value.encode("utf-8"),
                    aad=rule_revision_text_aad(rule.id, rev.number),
                )
                row.revisions.append(
                    RuleRevisionRow(
                        rule_id=rule.id,
                        number=rev.number,
                        text_ciphertext=ciphertext,
                        author_id=rev.author_id,
                        proposed_at=rev.proposed_at,
                        approved_by=approved,
                        effective_since=rev.effective_since,
                    )
                )

    @staticmethod
    def _touch_rule_aggregate(row: RuleRow) -> None:
        """Ensure ``rules.version`` increments for any aggregate write, including revision-only."""
        flag_modified(row, "status")

    async def add(self, rule: Rule) -> None:
        scope_contact = rule.scope.contact_id if isinstance(rule.scope, ContactScope) else None
        scope_pair = rule.scope.pair_id if isinstance(rule.scope, PairScope) else None
        row = RuleRow(
            id=rule.id,
            scope_contact_id=scope_contact,
            scope_pair_id=scope_pair,
            category=rule.category.value,
            status=rule.status.value,
            approver_ids=cast(list[UUID], sorted(rule.approvers)),
            created_at=rule.created_at,
            version=1,
            revisions=[],
        )
        self._session.add(row)
        self._registry.register(RuleRow, rule.id, row)
        dek = await self._scope_dek(rule.scope)
        await self._write_revisions(row, rule, dek)
        await flush_or_raise(self._session)

    async def update(self, rule: Rule) -> None:
        row = self._registry.require(RuleRow, rule.id)
        row.category = rule.category.value
        row.status = rule.status.value
        row.approver_ids = cast(list[UUID], sorted(rule.approvers))
        dek = await self._scope_dek(rule.scope)
        await self._write_revisions(row, rule, dek)
        self._touch_rule_aggregate(row)
        await flush_or_raise(self._session)

    async def delete(self, rule_id: RuleId) -> None:
        row = await self._session.get(RuleRow, rule_id)
        if row is not None:
            await self._session.delete(row)
        await flush_or_raise(self._session)


class SqlAlchemyInviteRepository:
    """Invite repository."""

    def __init__(self, session: AsyncSession, registry: RowRegistry) -> None:
        self._session = session
        self._registry = registry

    def _to_domain(self, row: InviteRow) -> Invite:
        return Invite(
            id=InviteId(row.id),
            inviter_id=UserId(row.inviter_id),
            contact_id=ContactId(row.contact_id),
            token_hash=InviteTokenHash.from_hex(row.token_hash),
            created_at=row.created_at,
            expires_at=row.expires_at,
            accepted_by=UserId(row.accepted_by) if row.accepted_by is not None else None,
            accepted_at=row.accepted_at,
        )

    async def get(self, invite_id: InviteId) -> Invite | None:
        row = await self._session.get(InviteRow, invite_id)
        if row is None:
            return None
        self._registry.register(InviteRow, row.id, row)
        return self._to_domain(row)

    async def get_by_token_hash(self, token_hash: InviteTokenHash) -> Invite | None:
        result = await self._session.execute(
            select(InviteRow).where(InviteRow.token_hash == token_hash.hex)
        )
        row = result.scalar_one_or_none()
        if row is None:
            return None
        self._registry.register(InviteRow, row.id, row)
        return self._to_domain(row)

    async def add(self, invite: Invite) -> None:
        row = InviteRow(
            id=invite.id,
            inviter_id=invite.inviter_id,
            contact_id=invite.contact_id,
            token_hash=invite.token_hash.hex,
            created_at=invite.created_at,
            expires_at=invite.expires_at,
            accepted_by=invite.accepted_by,
            accepted_at=invite.accepted_at,
            version=1,
        )
        self._session.add(row)
        self._registry.register(InviteRow, invite.id, row)
        await flush_or_raise(self._session)

    async def update(self, invite: Invite) -> None:
        row = self._registry.require(InviteRow, invite.id)
        row.accepted_by = invite.accepted_by
        row.accepted_at = invite.accepted_at
        await flush_or_raise(self._session)

    async def list_involving(self, user_id: UserId) -> list[Invite]:
        result = await self._session.execute(
            select(InviteRow).where(
                or_(InviteRow.inviter_id == user_id, InviteRow.accepted_by == user_id)
            )
        )
        invites: list[Invite] = []
        for row in result.scalars():
            self._registry.register(InviteRow, row.id, row)
            invites.append(self._to_domain(row))
        return invites

    async def delete(self, invite_id: InviteId) -> None:
        row = await self._session.get(InviteRow, invite_id)
        if row is not None:
            await self._session.delete(row)
        await flush_or_raise(self._session)


class SqlAlchemyUsageEventRepository:
    """Usage-event repository (append-only)."""

    def __init__(self, session: AsyncSession, registry: RowRegistry) -> None:
        self._session = session
        self._registry = registry

    def _to_domain(self, row: UsageEventRow) -> UsageEvent:
        return UsageEvent(
            id=UsageEventId(row.id),
            occurred_at=row.occurred_at,
            user_pseudonym=row.user_pseudonym,
            scenario=UsageScenario(row.scenario),
            surface=UsageSurface(row.surface),
            outcome=UsageOutcome(row.outcome),
            unavailable_kind=row.unavailable_kind,
            safety=row.safety,
            model=row.model,
            prompt_version=row.prompt_version,
            latency_ms=row.latency_ms,
            ttfc_ms=row.ttfc_ms,
            attempts=row.attempts,
            input_tokens=row.input_tokens,
            output_tokens=row.output_tokens,
            billable_tokens=row.billable_tokens,
            event_kind=UsageEventKind(row.event_kind),
            variant_firmness=None
            if row.variant_firmness is None
            else Firmness(row.variant_firmness),
            limit_kind=None if row.limit_kind is None else LimitKind(row.limit_kind),
        )

    async def get(self, event_id: UsageEventId) -> UsageEvent | None:
        row = await self._session.get(UsageEventRow, event_id)
        if row is None:
            return None
        self._registry.register(UsageEventRow, row.id, row)
        return self._to_domain(row)

    async def add(self, event: UsageEvent) -> None:
        row = UsageEventRow(
            id=event.id,
            occurred_at=event.occurred_at,
            user_pseudonym=event.user_pseudonym,
            scenario=event.scenario.value,
            surface=event.surface.value,
            outcome=event.outcome.value,
            unavailable_kind=event.unavailable_kind,
            safety=event.safety,
            model=event.model,
            prompt_version=event.prompt_version,
            latency_ms=event.latency_ms,
            ttfc_ms=event.ttfc_ms,
            attempts=event.attempts,
            input_tokens=event.input_tokens,
            output_tokens=event.output_tokens,
            billable_tokens=event.billable_tokens,
            event_kind=event.event_kind.value,
            variant_firmness=(
                None if event.variant_firmness is None else event.variant_firmness.value
            ),
            limit_kind=None if event.limit_kind is None else event.limit_kind.value,
        )
        self._session.add(row)
        self._registry.register(UsageEventRow, event.id, row)
        await flush_or_raise(self._session)

    async def delete_for_pseudonym(self, user_pseudonym: str) -> None:
        await self._session.execute(
            delete(UsageEventRow).where(UsageEventRow.user_pseudonym == user_pseudonym)
        )
        await flush_or_raise(self._session)

    async def sum_billable_for_day(self, day: date, timezone: str) -> int:
        """Sum billable tokens whose ``occurred_at`` falls in the product day."""
        result = await self._session.execute(
            text(
                """
                SELECT COALESCE(SUM(billable_tokens), 0)::bigint AS total
                FROM usage_events
                WHERE occurred_at >= timezone(:tz, CAST(:day AS timestamp without time zone))
                  AND occurred_at < timezone(
                      :tz,
                      CAST(:day AS timestamp without time zone) + interval '1 day'
                  )
                """
            ),
            {"day": day, "tz": timezone},
        )
        total = result.scalar_one()
        return int(total)


class SqlAlchemyRuleSuggestionRepository:
    """Rule suggestion repository with text encryption (user DEK)."""

    def __init__(self, session: AsyncSession, keys: KeyRing, registry: RowRegistry) -> None:
        self._session = session
        self._keys = keys
        self._registry = registry

    async def _encrypt_text(self, suggestion: RuleSuggestion) -> bytes:
        dek = await self._keys.user_dek(suggestion.user_id)
        return FieldCipher(dek).encrypt(
            suggestion.text.value.encode("utf-8"),
            aad=rule_suggestion_text_aad(suggestion.id),
        )

    async def _to_domain(self, row: RuleSuggestionRow) -> RuleSuggestion:
        dek = await self._keys.user_dek(row.user_id)
        plaintext = (
            FieldCipher(dek)
            .decrypt(
                row.text_ciphertext,
                aad=rule_suggestion_text_aad(row.id),
            )
            .decode("utf-8")
        )
        self._registry.register(RuleSuggestionRow, row.id, row)
        return RuleSuggestion(
            id=RuleSuggestionId(row.id),
            user_id=UserId(row.user_id),
            contact_id=ContactId(row.contact_id),
            source=SuggestionSource(row.source),
            category=RuleCategory(row.category),
            text=RuleText(plaintext),
            firmness=None if row.firmness is None else Firmness(row.firmness),
            status=SuggestionStatus(row.status),
            created_at=row.created_at,
            decided_at=row.decided_at,
        )

    async def get(self, suggestion_id: RuleSuggestionId) -> RuleSuggestion | None:
        row = await self._session.get(RuleSuggestionRow, suggestion_id)
        if row is None:
            return None
        return await self._to_domain(row)

    async def list_for_user(self, user_id: UserId) -> list[RuleSuggestion]:
        result = await self._session.execute(
            select(RuleSuggestionRow).where(RuleSuggestionRow.user_id == user_id)
        )
        return [await self._to_domain(row) for row in result.scalars()]

    async def list_pending_for_contact(
        self,
        user_id: UserId,
        contact_id: ContactId,
    ) -> list[RuleSuggestion]:
        result = await self._session.execute(
            select(RuleSuggestionRow).where(
                RuleSuggestionRow.user_id == user_id,
                RuleSuggestionRow.contact_id == contact_id,
                RuleSuggestionRow.status == SuggestionStatus.PENDING.value,
            )
        )
        return [await self._to_domain(row) for row in result.scalars()]

    async def get_tone(
        self,
        user_id: UserId,
        contact_id: ContactId,
        firmness: Firmness,
    ) -> RuleSuggestion | None:
        result = await self._session.execute(
            select(RuleSuggestionRow).where(
                RuleSuggestionRow.user_id == user_id,
                RuleSuggestionRow.contact_id == contact_id,
                RuleSuggestionRow.source == SuggestionSource.TONE.value,
                RuleSuggestionRow.firmness == firmness.value,
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            return None
        return await self._to_domain(row)

    async def has_pending_for_source(
        self,
        user_id: UserId,
        contact_id: ContactId,
        source: SuggestionSource,
    ) -> bool:
        result = await self._session.execute(
            select(func.count())
            .select_from(RuleSuggestionRow)
            .where(
                RuleSuggestionRow.user_id == user_id,
                RuleSuggestionRow.contact_id == contact_id,
                RuleSuggestionRow.source == source.value,
                RuleSuggestionRow.status == SuggestionStatus.PENDING.value,
            )
        )
        return int(result.scalar_one()) > 0

    async def add(self, suggestion: RuleSuggestion) -> None:
        ciphertext = await self._encrypt_text(suggestion)
        row = RuleSuggestionRow(
            id=suggestion.id,
            user_id=suggestion.user_id,
            contact_id=suggestion.contact_id,
            source=suggestion.source.value,
            category=suggestion.category.value,
            text_ciphertext=ciphertext,
            firmness=None if suggestion.firmness is None else suggestion.firmness.value,
            status=suggestion.status.value,
            created_at=suggestion.created_at,
            decided_at=suggestion.decided_at,
            version=1,
        )
        self._session.add(row)
        self._registry.register(RuleSuggestionRow, suggestion.id, row)
        await flush_or_raise(self._session)

    async def update(self, suggestion: RuleSuggestion) -> None:
        row = self._registry.require(RuleSuggestionRow, suggestion.id)
        row.status = suggestion.status.value
        row.decided_at = suggestion.decided_at
        await flush_or_raise(self._session)

    async def delete_for_user(self, user_id: UserId) -> None:
        await self._session.execute(
            delete(RuleSuggestionRow).where(RuleSuggestionRow.user_id == user_id)
        )
        await flush_or_raise(self._session)


class SqlAlchemyToneSignalRepository:
    """Tone signal repository (C0 firmness values, no encryption)."""

    def __init__(self, session: AsyncSession, registry: RowRegistry) -> None:
        self._session = session
        self._registry = registry

    def _to_domain(self, row: ToneSignalRow) -> ToneSignal:
        # Composite PK: contact_id is globally unique, used as registry key.
        self._registry.register(ToneSignalRow, row.contact_id, row)
        return ToneSignal(
            user_id=UserId(row.user_id),
            contact_id=ContactId(row.contact_id),
            values=tuple(Firmness(v) for v in row.values),
        )

    async def get(
        self,
        user_id: UserId,
        contact_id: ContactId,
    ) -> ToneSignal | None:
        existing = await self._session.get(ToneSignalRow, (user_id, contact_id))
        if existing is None:
            return None
        return self._to_domain(existing)

    async def lock_for_append(
        self,
        user_id: UserId,
        contact_id: ContactId,
    ) -> ToneSignal:
        await self._session.execute(
            pg_insert(ToneSignalRow)
            .values(user_id=user_id, contact_id=contact_id, values=[])
            .on_conflict_do_nothing(index_elements=["user_id", "contact_id"])
        )
        result = await self._session.execute(
            select(ToneSignalRow)
            .where(
                ToneSignalRow.user_id == user_id,
                ToneSignalRow.contact_id == contact_id,
            )
            .with_for_update()
        )
        locked = result.scalar_one()
        return self._to_domain(locked)

    async def list_for_user(self, user_id: UserId) -> list[ToneSignal]:
        result = await self._session.execute(
            select(ToneSignalRow).where(ToneSignalRow.user_id == user_id)
        )
        return [self._to_domain(row) for row in result.scalars()]

    async def upsert(self, signal: ToneSignal) -> None:
        row = await self._session.get(ToneSignalRow, (signal.user_id, signal.contact_id))
        values = [v.value for v in signal.values]
        if row is None:
            row = ToneSignalRow(
                user_id=signal.user_id,
                contact_id=signal.contact_id,
                values=values,
            )
            self._session.add(row)
        else:
            row.values = values
            flag_modified(row, "values")
        self._registry.register(ToneSignalRow, signal.contact_id, row)
        await flush_or_raise(self._session)

    async def delete_for_user(self, user_id: UserId) -> None:
        await self._session.execute(delete(ToneSignalRow).where(ToneSignalRow.user_id == user_id))
        await flush_or_raise(self._session)
