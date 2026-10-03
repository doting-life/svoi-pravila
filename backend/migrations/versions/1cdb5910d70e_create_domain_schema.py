"""create_domain_schema

Revision ID: 1cdb5910d70e
Revises:
Create Date: 2026-10-03 11:54:14.532057
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "1cdb5910d70e"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("age_confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("active_contact_id", sa.Uuid(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("telegram_user_id", name="uq_users_telegram_user_id"),
    )
    op.create_table(
        "pairs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("member_low", sa.Uuid(), nullable=False),
        sa.Column("member_high", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("member_low < member_high", name=op.f("ck_pairs_member_order")),
        sa.ForeignKeyConstraint(
            ["member_high"],
            ["users.id"],
            name=op.f("fk_pairs_member_high_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["member_low"],
            ["users.id"],
            name=op.f("fk_pairs_member_low_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_pairs")),
        sa.UniqueConstraint(
            "member_low",
            "member_high",
            name="uq_pairs_member_low_member_high",
        ),
    )
    op.create_table(
        "contacts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("label_ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("relationship", sa.Text(), nullable=False),
        sa.Column("pair_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "relationship IN ('partner', 'family', 'friend', 'work', 'other')",
            name=op.f("ck_contacts_relationship"),
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["users.id"],
            name=op.f("fk_contacts_owner_id_users"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["pair_id"],
            ["pairs.id"],
            name=op.f("fk_contacts_pair_id_pairs"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_contacts")),
        sa.UniqueConstraint("pair_id", "owner_id", name="uq_contacts_pair_id_owner_id"),
    )
    op.create_index(op.f("ix_contacts_owner_id"), "contacts", ["owner_id"], unique=False)
    op.create_foreign_key(
        op.f("fk_users_active_contact_id_contacts"),
        "users",
        "contacts",
        ["active_contact_id"],
        ["id"],
        ondelete="SET NULL",
        deferrable=True,
        initially="DEFERRED",
    )
    op.create_table(
        "user_keys",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("wrapped_dek", sa.LargeBinary(), nullable=False),
        sa.Column("kek_id", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_user_keys_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id", name=op.f("pk_user_keys")),
    )
    op.create_table(
        "consents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("text_version", sa.Text(), nullable=False),
        sa.Column("text_sha256", sa.String(length=64), nullable=False),
        sa.Column("granted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "kind IN ('personal_data', 'special_category')",
            name=op.f("ck_consents_kind"),
        ),
        sa.CheckConstraint(
            "text_sha256 ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_consents_text_sha256_hex"),
        ),
        sa.CheckConstraint(
            "revoked_at IS NULL OR revoked_at >= granted_at",
            name=op.f("ck_consents_revoked_after_granted"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_consents_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_consents")),
    )
    op.create_index(op.f("ix_consents_user_id"), "consents", ["user_id"], unique=False)
    op.create_table(
        "pair_keys",
        sa.Column("pair_id", sa.Uuid(), nullable=False),
        sa.Column("wrapped_dek", sa.LargeBinary(), nullable=False),
        sa.Column("kek_id", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["pair_id"],
            ["pairs.id"],
            name=op.f("fk_pair_keys_pair_id_pairs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("pair_id", name=op.f("pk_pair_keys")),
    )
    op.create_table(
        "rules",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("scope_contact_id", sa.Uuid(), nullable=True),
        sa.Column("scope_pair_id", sa.Uuid(), nullable=True),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("approver_ids", postgresql.ARRAY(sa.Uuid()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "category IN ('taboo_topic', 'how_to_ask', 'apology', 'conflict_protocol', 'other')",
            name=op.f("ck_rules_category"),
        ),
        sa.CheckConstraint(
            "status IN ('proposed', 'active', 'rejected', 'archived')",
            name=op.f("ck_rules_status"),
        ),
        sa.CheckConstraint(
            "(scope_contact_id IS NOT NULL AND scope_pair_id IS NULL) OR "
            "(scope_contact_id IS NULL AND scope_pair_id IS NOT NULL)",
            name=op.f("ck_rules_exactly_one_scope"),
        ),
        sa.CheckConstraint(
            "cardinality(approver_ids) BETWEEN 1 AND 2",
            name=op.f("ck_rules_approver_cardinality"),
        ),
        sa.ForeignKeyConstraint(
            ["scope_contact_id"],
            ["contacts.id"],
            name=op.f("fk_rules_scope_contact_id_contacts"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["scope_pair_id"],
            ["pairs.id"],
            name=op.f("fk_rules_scope_pair_id_pairs"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_rules")),
    )
    op.create_index("ix_rules_scope_contact_id", "rules", ["scope_contact_id"], unique=False)
    op.create_index("ix_rules_scope_pair_id", "rules", ["scope_pair_id"], unique=False)
    op.create_table(
        "rule_revisions",
        sa.Column("rule_id", sa.Uuid(), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("text_ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("author_id", sa.Uuid(), nullable=False),
        sa.Column("proposed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("approved_by", postgresql.ARRAY(sa.Uuid()), nullable=False),
        sa.Column("effective_since", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "cardinality(approved_by) BETWEEN 1 AND 2",
            name=op.f("ck_rule_revisions_approved_by_cardinality"),
        ),
        sa.CheckConstraint("number >= 1", name=op.f("ck_rule_revisions_revision_number")),
        sa.ForeignKeyConstraint(
            ["author_id"],
            ["users.id"],
            name=op.f("fk_rule_revisions_author_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["rule_id"],
            ["rules.id"],
            name=op.f("fk_rule_revisions_rule_id_rules"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("rule_id", "number", name=op.f("pk_rule_revisions")),
    )
    op.create_table(
        "invites",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("inviter_id", sa.Uuid(), nullable=False),
        sa.Column("contact_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_by", sa.Uuid(), nullable=True),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "token_hash ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_invites_token_hash_hex"),
        ),
        sa.CheckConstraint(
            "(accepted_by IS NULL AND accepted_at IS NULL) OR "
            "(accepted_by IS NOT NULL AND accepted_at IS NOT NULL)",
            name=op.f("ck_invites_accepted_pair"),
        ),
        sa.ForeignKeyConstraint(
            ["accepted_by"],
            ["users.id"],
            name=op.f("fk_invites_accepted_by_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["contact_id"],
            ["contacts.id"],
            name=op.f("fk_invites_contact_id_contacts"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["inviter_id"],
            ["users.id"],
            name=op.f("fk_invites_inviter_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_invites")),
        sa.UniqueConstraint("token_hash", name="uq_invites_token_hash"),
    )


def downgrade() -> None:
    op.drop_table("invites")
    op.drop_table("rule_revisions")
    op.drop_index("ix_rules_scope_pair_id", table_name="rules")
    op.drop_index("ix_rules_scope_contact_id", table_name="rules")
    op.drop_table("rules")
    op.drop_table("pair_keys")
    op.drop_index(op.f("ix_consents_user_id"), table_name="consents")
    op.drop_table("consents")
    op.drop_table("user_keys")
    op.drop_constraint(
        op.f("fk_users_active_contact_id_contacts"),
        "users",
        type_="foreignkey",
    )
    op.drop_index(op.f("ix_contacts_owner_id"), table_name="contacts")
    op.drop_table("contacts")
    op.drop_table("pairs")
    op.drop_table("users")
