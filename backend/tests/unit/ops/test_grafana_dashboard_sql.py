"""D1 — dashboard panel SQL may reference only analytics aggregate tables; program report row."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[4]
_DASHBOARD = _REPO_ROOT / "ops" / "grafana" / "dashboards" / "svoi-analytics.json"
_ALLOWED = frozenset(
    {
        "analytics_daily",
        "analytics_daily_scenario",
        "analytics_daily_llm",
        "analytics_daily_load",
        "analytics_cohorts",
        "llm_prices",
    }
)
_FORBIDDEN = frozenset({"usage_events", "job_runs", "users"})
_IDENT = re.compile(r"\b([a-z_][a-z0-9_]*)\b", re.IGNORECASE)


def _panel_raw_sql(dashboard: dict[str, object]) -> list[str]:
    panels = dashboard.get("panels")
    assert isinstance(panels, list)
    sqls: list[str] = []
    for panel in panels:
        assert isinstance(panel, dict)
        if panel.get("type") == "row":
            continue
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


_ROW_TITLE = "Отчёт программы"
_SECOND_ROW_TITLE = "Продукт и качество"
_REPORT_PANELS = (
    ("stat", "Прошли целевой сценарий, всего"),
    ("stat", "Вызовы LLM на активного пользователя"),
    ("stat", "LLM-cost на целевое действие, ₽"),
    ("stat", "LLM-cost за период, ₽"),
    ("stat", "Модели без цены"),
    ("timeseries", "LLM-cost, ₽ в день"),
    ("timeseries", "Вызовы LLM на активного пользователя"),
    ("timeseries", "LLM-cost на целевое действие, ₽"),
    ("timeseries", "Пиковые TPM и TPS"),
    ("table", "Сводка по дням"),
)
_EXISTING_START = 2 + len(_REPORT_PANELS)
_EXISTING_PANELS = (
    (1, "DAU (активные пользователи)"),
    (2, "Обращения"),
    (3, "Обращения на DAU"),
    (4, "Новые пользователи"),
    (5, "Удержание D1% по дню когорты"),
    (6, "Удержание D7% по дню когорты"),
    (7, "Обращения по сценарию × поверхности"),
    (8, "Выборы варианта (chosen) в inline по дням"),
    (9, "Доля ошибок генерации"),
    (10, "Латентность p50 / p95 и TTFC p95 по сценарию × поверхности"),
    (11, "Платные токены по дням и сценариям"),
    (12, "Упёрлись в лимит"),
    (13, "Пользователи, исчерпавшие квоту"),
    (14, "Расход токенов и бюджет"),
)
_DAILY_TABLE_COLUMNS = (
    "day",
    "dau",
    "new_users",
    "target_users_total",
    "target_actions",
    "target_actions_per_dau",
    "generations",
    "generation_errors",
    "error_rate",
    "users_limited",
    "llm_calls",
    "llm_calls_per_active_user",
    "billable_tokens",
    "llm_cost_rub",
    "llm_cost_per_target_action_rub",
)


def _panels() -> list[dict[str, object]]:
    panels = json.loads(_DASHBOARD.read_text())["panels"]
    assert isinstance(panels, list)
    return panels


def _sql(panel: dict[str, object]) -> str:
    targets = panel["targets"]
    assert isinstance(targets, list)
    raw = targets[0]["rawSql"]
    assert isinstance(raw, str)
    return raw


def _grid(panel: dict[str, object], key: str) -> int:
    grid = panel["gridPos"]
    assert isinstance(grid, dict)
    value = grid[key]
    assert isinstance(value, int)
    return value


def _report_panels() -> list[dict[str, object]]:
    return _panels()[1 : 1 + len(_REPORT_PANELS)]


def _by_title(title: str, panel_type: str) -> dict[str, object]:
    return next(
        panel
        for panel in _report_panels()
        if panel["title"] == title and panel["type"] == panel_type
    )


@pytest.mark.unit
def test_exactly_two_rows_report_first_and_product_second() -> None:
    panels = _panels()
    rows = [panel for panel in panels if panel["type"] == "row"]
    assert [(row["title"], row["collapsed"]) for row in rows] == [
        (_ROW_TITLE, False),
        (_SECOND_ROW_TITLE, False),
    ]
    assert panels[0] is rows[0]
    assert panels[1 + len(_REPORT_PANELS)] is rows[1]
    assert [(panel["type"], panel["title"]) for panel in _report_panels()] == list(_REPORT_PANELS)


@pytest.mark.unit
def test_panel_ids_are_unique_and_existing_ones_keep_id_and_order() -> None:
    panels = _panels()
    ids = [panel["id"] for panel in panels]
    assert len(set(ids)) == len(ids)
    existing = panels[_EXISTING_START:]
    assert [(panel["id"], panel["title"]) for panel in existing] == list(_EXISTING_PANELS)
    new_ids = ids[:_EXISTING_START]
    assert all(isinstance(item, int) and item >= 15 for item in new_ids)


@pytest.mark.unit
def test_every_panel_sits_below_its_row_in_order() -> None:
    panels = _panels()
    report_bottom = max(
        _grid(panel, "y") + _grid(panel, "h") for panel in panels[: 1 + len(_REPORT_PANELS)]
    )
    second_row = panels[1 + len(_REPORT_PANELS)]
    assert _grid(second_row, "y") == report_bottom
    assert _grid(second_row, "h") == 1
    existing = panels[_EXISTING_START:]
    assert min(_grid(panel, "y") for panel in existing) == report_bottom + 1


@pytest.mark.unit
def test_report_panels_filter_by_the_time_picker_with_time_filter() -> None:
    for panel in _report_panels():
        sql = _sql(panel)
        assert "$__timeFilter(day)" in sql, panel["title"]
        assert "$__timeFrom()" not in sql, panel["title"]
        assert "$__timeTo()" not in sql, panel["title"]


@pytest.mark.unit
def test_target_users_stat_takes_the_last_day_inside_the_filter() -> None:
    sql = _sql(_by_title("Прошли целевой сценарий, всего", "stat"))
    assert "WHERE $__timeFilter(day)\nORDER BY day DESC\nLIMIT 1" in sql


@pytest.mark.unit
def test_models_without_price_stat_turns_red_above_zero() -> None:
    panel = _by_title("Модели без цены", "stat")
    config = panel["fieldConfig"]
    assert isinstance(config, dict)
    steps = config["defaults"]["thresholds"]["steps"]
    assert [(step["color"], step["value"]) for step in steps] == [("green", None), ("red", 1)]
    assert "analytics_daily_llm" in _sql(panel)
    assert "price_rub_per_1k IS NULL" in _sql(panel)


@pytest.mark.unit
def test_cost_per_day_is_stacked_by_scenario() -> None:
    panel = _by_title("LLM-cost, ₽ в день", "timeseries")
    config = panel["fieldConfig"]
    assert isinstance(config, dict)
    assert config["defaults"]["custom"]["stacking"]["mode"] == "normal"
    assert "scenario AS metric" in _sql(panel)


@pytest.mark.unit
def test_peak_panel_puts_tps_on_a_second_axis() -> None:
    panel = _by_title("Пиковые TPM и TPS", "timeseries")
    config = panel["fieldConfig"]
    assert isinstance(config, dict)
    (override,) = config["overrides"]
    assert override["matcher"]["options"] == "Пиковый TPS"
    assert override["properties"] == [{"id": "custom.axisPlacement", "value": "right"}]
    assert "analytics_daily_load" in _sql(panel)


@pytest.mark.unit
def test_daily_table_has_the_columns_of_the_daily_csv() -> None:
    sql = _sql(_by_title("Сводка по дням", "table"))
    head, _, _tail = sql.partition("\nFROM ")
    select_list = head.removeprefix("SELECT\n")
    aliases = [line.strip().rstrip(",").rsplit(" AS ", 1)[-1] for line in select_list.splitlines()]
    # Multi-line expressions are single lines in the query; the alias or the bare column is last.
    assert tuple(aliases) == _DAILY_TABLE_COLUMNS
