"""rewrite_variant_kind

Rename firmness to variant kind (gentle/balanced/firm to softer/firmer/check) and
collapse the soften and help_say scenarios into rewrite.

Revision ID: c7d8e9f0a1b2
Revises: b3c4d5e6f7a8
Create Date: 2026-10-13 12:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c7d8e9f0a1b2"
down_revision: str | Sequence[str] | None = "b3c4d5e6f7a8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TONE_INDEX = "uq_rule_suggestions_tone_user_contact_firmness"
_TONE_INDEX_NEW = "uq_rule_suggestions_tone_user_contact_variant_kind"


def _shape(column: str, values: str) -> str:
    return (
        "("
        "event_kind = 'generation' AND outcome = 'screened' AND safety = 'crisis' "
        "AND model IS NULL AND prompt_version IS NULL AND attempts = 0 "
        "AND input_tokens = 0 AND output_tokens = 0 AND billable_tokens = 0 "
        f"AND ttfc_ms IS NULL AND unavailable_kind IS NULL AND {column} IS NULL "
        "AND limit_kind IS NULL"
        ") OR ("
        "event_kind = 'generation' AND outcome = 'limited' "
        "AND limit_kind IN ('user_quota', 'global_budget') "
        "AND safety IS NULL AND model IS NULL AND prompt_version IS NULL "
        "AND attempts = 0 AND input_tokens = 0 AND output_tokens = 0 "
        "AND billable_tokens = 0 AND ttfc_ms IS NULL AND unavailable_kind IS NULL "
        f"AND {column} IS NULL"
        ") OR ("
        "event_kind = 'generation' AND outcome NOT IN ('screened', 'limited') "
        "AND model IS NOT NULL AND model <> '' "
        "AND prompt_version IS NOT NULL AND prompt_version <> '' "
        f"AND attempts >= 1 AND {column} IS NULL AND limit_kind IS NULL"
        ") OR ("
        "event_kind = 'result_chosen' AND model IS NULL AND prompt_version IS NULL "
        "AND attempts = 0 AND latency_ms = 0 AND ttfc_ms IS NULL "
        "AND input_tokens = 0 AND output_tokens = 0 AND billable_tokens = 0 "
        "AND safety IS NULL AND unavailable_kind IS NULL AND outcome = 'ok' "
        f"AND {column} IN {values} AND limit_kind IS NULL"
        ")"
    )


_OLD_VALUES = "('gentle', 'balanced', 'firm')"
_NEW_VALUES = "('check', 'softer', 'firmer')"
_OLD_SCENARIOS = "scenario IN ('decode', 'soften', 'help_say', 'suggest_rule')"
_NEW_SCENARIOS = "scenario IN ('decode', 'rewrite', 'suggest_rule')"

_REMAP_ARRAY = (
    'UPDATE tone_signals SET "values" = ARRAY('
    "SELECT CASE t.v {cases} END "
    'FROM unnest("values") WITH ORDINALITY AS t(v, ord) '
    "WHERE t.v IN {keep} ORDER BY t.ord)"
)


def upgrade() -> None:
    # rule_suggestions / tone_signals
    op.drop_constraint(
        op.f("ck_rule_suggestions_source_firmness"), "rule_suggestions", type_="check"
    )
    op.drop_constraint(op.f("ck_rule_suggestions_firmness"), "rule_suggestions", type_="check")
    op.drop_index(_TONE_INDEX, table_name="rule_suggestions")
    op.execute("DELETE FROM rule_suggestions WHERE firmness = 'balanced'")
    op.execute(
        "UPDATE rule_suggestions SET firmness = CASE firmness "
        "WHEN 'gentle' THEN 'softer' WHEN 'firm' THEN 'firmer' END "
        "WHERE firmness IS NOT NULL"
    )
    op.alter_column("rule_suggestions", "firmness", new_column_name="variant_kind")
    op.create_check_constraint(
        "variant_kind",
        "rule_suggestions",
        "variant_kind IS NULL OR variant_kind IN ('softer', 'firmer')",
    )
    op.create_check_constraint(
        "source_variant_kind",
        "rule_suggestions",
        "(source = 'tone' AND variant_kind IS NOT NULL) OR "
        "(source = 'decode' AND variant_kind IS NULL)",
    )
    op.create_index(
        _TONE_INDEX_NEW,
        "rule_suggestions",
        ["user_id", "contact_id", "variant_kind"],
        unique=True,
        postgresql_where=sa.text("source = 'tone'"),
    )
    op.execute(
        _REMAP_ARRAY.format(
            cases="WHEN 'gentle' THEN 'softer' WHEN 'firm' THEN 'firmer'",
            keep="('gentle', 'firm')",
        )
    )
    op.execute('DELETE FROM tone_signals WHERE cardinality("values") = 0')

    # usage_events
    op.drop_constraint(
        op.f("ck_usage_events_usage_event_kind_shape"), "usage_events", type_="check"
    )
    op.drop_constraint(
        op.f("ck_usage_events_usage_variant_firmness"), "usage_events", type_="check"
    )
    op.drop_constraint(op.f("ck_usage_events_usage_scenario"), "usage_events", type_="check")
    op.execute("DELETE FROM usage_events WHERE variant_firmness = 'balanced'")
    op.execute(
        "UPDATE usage_events SET variant_firmness = CASE variant_firmness "
        "WHEN 'gentle' THEN 'softer' WHEN 'firm' THEN 'firmer' END "
        "WHERE variant_firmness IS NOT NULL"
    )
    op.execute(
        "UPDATE usage_events SET scenario = 'rewrite' WHERE scenario IN ('soften', 'help_say')"
    )
    op.alter_column("usage_events", "variant_firmness", new_column_name="variant_kind")
    op.create_check_constraint(
        "usage_variant_kind",
        "usage_events",
        f"variant_kind IS NULL OR variant_kind IN {_NEW_VALUES}",
    )
    op.create_check_constraint("usage_scenario", "usage_events", _NEW_SCENARIOS)
    op.create_check_constraint(
        "usage_event_kind_shape", "usage_events", _shape("variant_kind", _NEW_VALUES)
    )

    # analytics_daily_scenario: slices of the two old scenarios cannot be merged
    # exactly (distinct users, percentiles); the daily job recomputes them from
    # usage_events.
    op.drop_constraint(
        op.f("ck_analytics_daily_scenario_usage_scenario"),
        "analytics_daily_scenario",
        type_="check",
    )
    op.execute("DELETE FROM analytics_daily_scenario WHERE scenario IN ('soften', 'help_say')")
    op.create_check_constraint("usage_scenario", "analytics_daily_scenario", _NEW_SCENARIOS)


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_analytics_daily_scenario_usage_scenario"),
        "analytics_daily_scenario",
        type_="check",
    )
    op.execute("DELETE FROM analytics_daily_scenario WHERE scenario = 'rewrite'")
    op.create_check_constraint("usage_scenario", "analytics_daily_scenario", _OLD_SCENARIOS)

    op.drop_constraint(
        op.f("ck_usage_events_usage_event_kind_shape"), "usage_events", type_="check"
    )
    op.drop_constraint(op.f("ck_usage_events_usage_scenario"), "usage_events", type_="check")
    op.drop_constraint(op.f("ck_usage_events_usage_variant_kind"), "usage_events", type_="check")
    op.execute("DELETE FROM usage_events WHERE variant_kind = 'check'")
    op.execute(
        "UPDATE usage_events SET variant_kind = CASE variant_kind "
        "WHEN 'softer' THEN 'gentle' WHEN 'firmer' THEN 'firm' END "
        "WHERE variant_kind IS NOT NULL"
    )
    op.execute("UPDATE usage_events SET scenario = 'soften' WHERE scenario = 'rewrite'")
    op.alter_column("usage_events", "variant_kind", new_column_name="variant_firmness")
    op.create_check_constraint(
        "usage_variant_firmness",
        "usage_events",
        f"variant_firmness IS NULL OR variant_firmness IN {_OLD_VALUES}",
    )
    op.create_check_constraint("usage_scenario", "usage_events", _OLD_SCENARIOS)
    op.create_check_constraint(
        "usage_event_kind_shape", "usage_events", _shape("variant_firmness", _OLD_VALUES)
    )

    op.execute(
        _REMAP_ARRAY.format(
            cases="WHEN 'softer' THEN 'gentle' WHEN 'firmer' THEN 'firm'",
            keep="('softer', 'firmer')",
        )
    )
    op.drop_index(_TONE_INDEX_NEW, table_name="rule_suggestions")
    op.drop_constraint(
        op.f("ck_rule_suggestions_source_variant_kind"), "rule_suggestions", type_="check"
    )
    op.drop_constraint(op.f("ck_rule_suggestions_variant_kind"), "rule_suggestions", type_="check")
    op.execute(
        "UPDATE rule_suggestions SET variant_kind = CASE variant_kind "
        "WHEN 'softer' THEN 'gentle' WHEN 'firmer' THEN 'firm' END "
        "WHERE variant_kind IS NOT NULL"
    )
    op.alter_column("rule_suggestions", "variant_kind", new_column_name="firmness")
    op.create_check_constraint(
        "firmness",
        "rule_suggestions",
        "firmness IS NULL OR firmness IN ('gentle', 'balanced', 'firm')",
    )
    op.create_check_constraint(
        "source_firmness",
        "rule_suggestions",
        "(source = 'tone' AND firmness IS NOT NULL) OR (source = 'decode' AND firmness IS NULL)",
    )
    op.create_index(
        _TONE_INDEX,
        "rule_suggestions",
        ["user_id", "contact_id", "firmness"],
        unique=True,
        postgresql_where=sa.text("source = 'tone'"),
    )
