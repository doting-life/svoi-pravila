"""Ops dashboard PromQL may reference only ``sp_`` or process collectors."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[4]
_DASHBOARD = _REPO_ROOT / "ops" / "grafana" / "dashboards" / "svoi-ops.json"
_METRIC_LIKE = re.compile(r"\b([a-zA-Z_][a-zA-Z0-9_]*_[a-zA-Z0-9_]*)\b")
_LABEL_BLOCK = re.compile(r"\{[^}]*\}")
_BY_BLOCK = re.compile(r"\bby\s*\([^)]*\)")
_ALLOWED_PREFIXES = ("sp_", "process_", "python_")
_PROMQL_FUNCS = frozenset(
    {
        "histogram_quantile",
        "clamp_min",
        "clamp_max",
        "group_left",
        "group_right",
    }
)


def _panel_exprs(dashboard: dict[str, object]) -> list[str]:
    panels = dashboard.get("panels")
    assert isinstance(panels, list)
    exprs: list[str] = []
    for panel in panels:
        assert isinstance(panel, dict)
        targets = panel.get("targets")
        assert isinstance(targets, list)
        for target in targets:
            assert isinstance(target, dict)
            expr = target.get("expr")
            assert isinstance(expr, str) and expr.strip(), panel.get("title")
            exprs.append(expr)
    return exprs


def _metric_names(expr: str) -> set[str]:
    stripped = _LABEL_BLOCK.sub("", expr)
    stripped = _BY_BLOCK.sub("", stripped)
    return {name for name in _METRIC_LIKE.findall(stripped) if name not in _PROMQL_FUNCS}


@pytest.mark.unit
def test_ops_dashboard_queries_only_sp_or_process_metrics() -> None:
    dashboard = json.loads(_DASHBOARD.read_text())
    assert dashboard["uid"] == "svoi-ops"
    exprs = _panel_exprs(dashboard)
    assert len(exprs) >= 10
    for expr in exprs:
        metrics = _metric_names(expr)
        assert metrics, expr
        for name in metrics:
            assert name.startswith(_ALLOWED_PREFIXES), (name, expr)
