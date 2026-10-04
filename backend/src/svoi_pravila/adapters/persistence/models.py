"""SQLAlchemy ORM row models (data mapper — separate from domain entities)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    MetaData,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import DeclarativeBase, Mapped, declared_attr, mapped_column, relationship

NAMING_CONVENTION: dict[str, str] = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Declarative base for ORM models."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class UserRow(Base):
    """Persistence row for ``User``."""

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    age_confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    active_contact_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "contacts.id",
            ondelete="SET NULL",
            deferrable=True,
            initially="DEFERRED",
            use_alter=True,
        ),
        nullable=True,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __table_args__ = (UniqueConstraint("telegram_user_id", name="uq_users_telegram_user_id"),)

    @declared_attr.directive
    @classmethod
    def __mapper_args__(cls) -> dict[str, object]:
        return {"version_id_col": cls.__table__.c.version}


class UserKeyRow(Base):
    """Wrapped DEK for a user."""

    __tablename__ = "user_keys"

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    wrapped_dek: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    kek_id: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ConsentRow(Base):
    """Persistence row for ``Consent``."""

    __tablename__ = "consents"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    text_version: Mapped[str] = mapped_column(Text, nullable=False)
    text_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "kind IN ('personal_data', 'special_category')",
            name="kind",
        ),
        CheckConstraint(
            "text_sha256 ~ '^[0-9a-f]{64}$'",
            name="text_sha256_hex",
        ),
        CheckConstraint(
            "revoked_at IS NULL OR revoked_at >= granted_at",
            name="revoked_after_granted",
        ),
    )


class PairRow(Base):
    """Persistence row for ``Pair``."""

    __tablename__ = "pairs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    member_low: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    member_high: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint("member_low < member_high", name="member_order"),
        UniqueConstraint(
            "member_low",
            "member_high",
            name="uq_pairs_member_low_member_high",
        ),
    )


class PairKeyRow(Base):
    """Wrapped DEK for a pair."""

    __tablename__ = "pair_keys"

    pair_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("pairs.id", ondelete="CASCADE"),
        primary_key=True,
    )
    wrapped_dek: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    kek_id: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ContactRow(Base):
    """Persistence row for ``Contact``."""

    __tablename__ = "contacts"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    label_ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    relationship: Mapped[str] = mapped_column(Text, nullable=False)
    pair_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("pairs.id", ondelete="RESTRICT"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __table_args__ = (
        CheckConstraint(
            "relationship IN ('partner', 'family', 'friend', 'work', 'other')",
            name="relationship",
        ),
        UniqueConstraint("pair_id", "owner_id", name="uq_contacts_pair_id_owner_id"),
    )

    @declared_attr.directive
    @classmethod
    def __mapper_args__(cls) -> dict[str, object]:
        return {"version_id_col": cls.__table__.c.version}


class RuleRow(Base):
    """Persistence row for ``Rule`` aggregate header."""

    __tablename__ = "rules"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    scope_contact_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("contacts.id", ondelete="CASCADE"),
        nullable=True,
    )
    scope_pair_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("pairs.id", ondelete="RESTRICT"),
        nullable=True,
    )
    category: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    approver_ids: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(Uuid(as_uuid=True)), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    revisions: Mapped[list[RuleRevisionRow]] = relationship(
        back_populates="rule",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    __table_args__ = (
        CheckConstraint(
            "(scope_contact_id IS NOT NULL AND scope_pair_id IS NULL) OR "
            "(scope_contact_id IS NULL AND scope_pair_id IS NOT NULL)",
            name="exactly_one_scope",
        ),
        CheckConstraint(
            "category IN ('taboo_topic', 'how_to_ask', 'apology', 'conflict_protocol', 'other')",
            name="category",
        ),
        CheckConstraint(
            "status IN ('proposed', 'active', 'rejected', 'archived')",
            name="status",
        ),
        CheckConstraint(
            "cardinality(approver_ids) BETWEEN 1 AND 2",
            name="approver_cardinality",
        ),
        Index("ix_rules_scope_contact_id", "scope_contact_id"),
        Index("ix_rules_scope_pair_id", "scope_pair_id"),
    )

    @declared_attr.directive
    @classmethod
    def __mapper_args__(cls) -> dict[str, object]:
        return {"version_id_col": cls.__table__.c.version}


class RuleRevisionRow(Base):
    """Persistence row for one rule revision."""

    __tablename__ = "rule_revisions"

    rule_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("rules.id", ondelete="CASCADE"),
        primary_key=True,
    )
    number: Mapped[int] = mapped_column(Integer, primary_key=True)
    text_ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    author_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    proposed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    approved_by: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(Uuid(as_uuid=True)), nullable=False)
    effective_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    rule: Mapped[RuleRow] = relationship(back_populates="revisions")

    __table_args__ = (
        CheckConstraint(
            "cardinality(approved_by) BETWEEN 1 AND 2",
            name="approved_by_cardinality",
        ),
        CheckConstraint("number >= 1", name="revision_number"),
    )


class InviteRow(Base):
    """Persistence row for ``Invite``."""

    __tablename__ = "invites"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    inviter_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    contact_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("contacts.id", ondelete="CASCADE"),
        nullable=False,
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accepted_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True,
    )
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_invites_token_hash"),
        CheckConstraint(
            "token_hash ~ '^[0-9a-f]{64}$'",
            name="token_hash_hex",
        ),
        CheckConstraint(
            "(accepted_by IS NULL AND accepted_at IS NULL) OR "
            "(accepted_by IS NOT NULL AND accepted_at IS NOT NULL)",
            name="accepted_pair",
        ),
    )

    @declared_attr.directive
    @classmethod
    def __mapper_args__(cls) -> dict[str, object]:
        return {"version_id_col": cls.__table__.c.version}


class UsageEventRow(Base):
    """C0 usage-event persistence row."""

    __tablename__ = "usage_events"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    user_pseudonym: Mapped[str] = mapped_column(String(64), nullable=False)
    scenario: Mapped[str] = mapped_column(Text(), nullable=False)
    surface: Mapped[str] = mapped_column(Text(), nullable=False)
    outcome: Mapped[str] = mapped_column(Text(), nullable=False)
    unavailable_kind: Mapped[str | None] = mapped_column(Text(), nullable=True)
    safety: Mapped[str | None] = mapped_column(Text(), nullable=True)
    model: Mapped[str | None] = mapped_column(Text(), nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(Text(), nullable=True)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    ttfc_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    billable_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    event_kind: Mapped[str] = mapped_column(Text(), nullable=False)
    variant_firmness: Mapped[str | None] = mapped_column(Text(), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "user_pseudonym ~ '^[0-9a-f]{64}$'",
            name="user_pseudonym_hex",
        ),
        CheckConstraint(
            "scenario IN ('decode', 'soften', 'help_say')",
            name="usage_scenario",
        ),
        CheckConstraint(
            "surface IN ('dm', 'inline', 'miniapp')",
            name="usage_surface",
        ),
        CheckConstraint(
            "outcome IN ('ok', 'invalid_output', 'refused', 'unavailable', 'screened')",
            name="usage_outcome",
        ),
        CheckConstraint(
            "event_kind IN ('generation', 'result_chosen')",
            name="usage_event_kind",
        ),
        CheckConstraint(
            "variant_firmness IS NULL OR variant_firmness IN ('gentle', 'balanced', 'firm')",
            name="usage_variant_firmness",
        ),
        CheckConstraint(
            "("
            "event_kind = 'generation' AND outcome = 'screened' AND safety = 'crisis' "
            "AND model IS NULL AND prompt_version IS NULL AND attempts = 0 "
            "AND input_tokens = 0 AND output_tokens = 0 AND billable_tokens = 0 "
            "AND ttfc_ms IS NULL AND unavailable_kind IS NULL AND variant_firmness IS NULL"
            ") OR ("
            "event_kind = 'generation' AND outcome <> 'screened' "
            "AND model IS NOT NULL AND model <> '' "
            "AND prompt_version IS NOT NULL AND prompt_version <> '' "
            "AND attempts >= 1 AND variant_firmness IS NULL"
            ") OR ("
            "event_kind = 'result_chosen' AND model IS NULL AND prompt_version IS NULL "
            "AND attempts = 0 AND latency_ms = 0 AND ttfc_ms IS NULL "
            "AND input_tokens = 0 AND output_tokens = 0 AND billable_tokens = 0 "
            "AND safety IS NULL AND unavailable_kind IS NULL AND outcome = 'ok' "
            "AND variant_firmness IN ('gentle', 'balanced', 'firm')"
            ")",
            name="usage_event_kind_shape",
        ),
        CheckConstraint("latency_ms >= 0", name="usage_latency"),
        CheckConstraint("input_tokens >= 0", name="usage_input_tokens"),
        CheckConstraint("output_tokens >= 0", name="usage_output_tokens"),
        CheckConstraint("billable_tokens >= 0", name="usage_billable_tokens"),
        Index("ix_usage_events_occurred_at", "occurred_at"),
        Index(
            "ix_usage_events_user_pseudonym_occurred_at",
            "user_pseudonym",
            "occurred_at",
        ),
    )
