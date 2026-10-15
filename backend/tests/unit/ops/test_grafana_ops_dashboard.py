"""Ops dashboard PromQL may reference only application, process, host and alert series."""

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
_ALLOWED_PREFIXES = ("sp_", "process_", "python_", "node_")
_ALLOWED_NAMES = frozenset({"ALERTS_FOR_STATE"})
_SERVER_ROW = "Сервер и алерты"
_SERVER_PANELS = (
    "CPU, %",
    "Память: занято, %",
    "Диск: свободно, % (по разделам)",
    "Последний успешный бэкап: возраст, ч",
    "Сработавшие алерты",
)
_SERVER_METRICS = frozenset(
    {
        "node_cpu_seconds_total",
        "node_memory_MemAvailable_bytes",
        "node_memory_MemTotal_bytes",
        "node_filesystem_avail_bytes",
        "node_filesystem_size_bytes",
        "node_filesystem_readonly",
        "sp_backup_last_success_timestamp_seconds",
        "ALERTS_FOR_STATE",
    }
)
_BACKUP_AGE = '(time() - sp_backup_last_success_timestamp_seconds{kind="daily"}) / 3600'
_REAL_FILESYSTEMS = 'fstype=~"ext4|xfs|btrfs"'
_PROCESS_PANEL = "Процесс: RSS и CPU"

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
        if panel.get("type") == "row":
            continue
        targets = panel.get("targets")
        assert isinstance(targets, list)
        for target in targets:
            assert isinstance(target, dict)
            expr = target.get("expr")
            assert isinstance(expr, str) and expr.strip(), panel.get("title")
            exprs.append(expr)
    return exprs


def _server_row_panels(dashboard: dict[str, object]) -> dict[str, list[str]]:
    """Panel title -> target expressions, for the panels after the «Сервер и алерты» row."""
    panels = dashboard.get("panels")
    assert isinstance(panels, list)
    titles = [panel["title"] for panel in panels]
    start = titles.index(_SERVER_ROW)
    assert panels[start]["type"] == "row"
    return {
        panel["title"]: [target["expr"] for target in panel["targets"]]
        for panel in panels[start + 1 :]
    }


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
            assert name.startswith(_ALLOWED_PREFIXES) or name in _ALLOWED_NAMES, (name, expr)


@pytest.mark.unit
def test_ops_dashboard_server_row_has_the_five_panels() -> None:
    row = _server_row_panels(json.loads(_DASHBOARD.read_text()))
    assert tuple(row) == _SERVER_PANELS
    used: set[str] = set()
    for exprs in row.values():
        for expr in exprs:
            used |= _metric_names(expr)
    assert used == _SERVER_METRICS


@pytest.mark.unit
def test_ops_dashboard_server_row_queries() -> None:
    row = _server_row_panels(json.loads(_DASHBOARD.read_text()))
    (disk,) = row["Диск: свободно, % (по разделам)"]
    assert _REAL_FILESYSTEMS in disk
    assert "node_filesystem_readonly" in disk
    assert row["Последний успешный бэкап: возраст, ч"] == [_BACKUP_AGE]
    (alerts,) = row["Сработавшие алерты"]
    assert 'ALERTS{alertstate="firing"}' in alerts


@pytest.mark.unit
def test_ops_dashboard_process_panel_is_limited_to_the_api_job() -> None:
    panels = json.loads(_DASHBOARD.read_text())["panels"]
    (panel,) = [panel for panel in panels if panel["title"] == _PROCESS_PANEL]
    exprs = [target["expr"] for target in panel["targets"]]
    assert exprs == [
        'process_resident_memory_bytes{job="api"}',
        'rate(process_cpu_seconds_total{job="api"}[1m])',
    ]
