"""D1 — dashboard panel SQL may reference only analytics aggregate tables."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[4]
_DASHBOARD = _REPO_ROOT / "ops" / "grafana" / "dashboards" / "svoi-analytics.json"
_ALLOWED = frozenset({"analytics_daily", "analytics_daily_scenario", "analytics_cohorts"})
_FORBIDDEN = frozenset({"usage_events", "job_runs", "users"})
_IDENT = re.compile(r"\b([a-z_][a-z0-9_]*)\b", re.IGNORECASE)


def _panel_raw_sql(dashboard: dict[str, object]) -> list[str]:
    panels = dashboard.get("panels")
    assert isinstance(panels, list)
    sqls: list[str] = []
    for panel in panels:
        assert isinstance(panel, dict)
        targets = panel.get("targets")
        assert isinstance(targets, list)
        for target in targets:
            assert isinstance(target, dict)
            raw = target.get("rawSql")
            assert isinstance(raw, str) and raw.strip(), panel.get("title")
            sqls.append(raw)
    return sqls


def _referenced_tables(sql: str) -> set[str]:
    lower = sql.lower()
    found: set[str] = set()
    for match in _IDENT.finditer(lower):
        name = match.group(1)
        if name in _ALLOWED or name in _FORBIDDEN:
            found.add(name)
    return found


@pytest.mark.unit
def test_dashboard_sql_references_only_aggregate_tables() -> None:
    dashboard = json.loads(_DASHBOARD.read_text())
    assert dashboard["uid"] == "svoi-analytics"
    sqls = _panel_raw_sql(dashboard)
    assert len(sqls) >= 8
    for sql in sqls:
        tables = _referenced_tables(sql)
        assert tables, sql
        assert tables <= _ALLOWED, tables
        assert not (tables & _FORBIDDEN), tables
