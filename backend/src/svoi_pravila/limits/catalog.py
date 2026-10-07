"""Shared user-facing limit copy for bot and mini-app API (ADR-0009)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from importlib import resources
from typing import Any
from zoneinfo import ZoneInfo


def format_reset_hhmm(resets_at: datetime, tz_name: str) -> str:
    """Local wall-clock ``HH:MM`` for user-facing limit copy."""
    local = resets_at.astimezone(ZoneInfo(tz_name))
    return f"{local.hour:02d}:{local.minute:02d}"


@dataclass(frozen=True, slots=True)
class LimitsCatalog:
    """Russian templates with a ``{reset_time}`` placeholder (HH:MM local)."""

    user_quota: str
    service_budget: str

    def user_quota_message(self, reset_time: str) -> str:
        """Render the per-user daily quota message."""
        return self.user_quota.format(reset_time=reset_time)

    def service_budget_message(self, reset_time: str) -> str:
        """Render the global service budget message."""
        return self.service_budget.format(reset_time=reset_time)


def load_limits_catalog() -> LimitsCatalog:
    """Load ``limits/ru.json``; malformed catalogs fail at startup."""
    raw = resources.files(__package__).joinpath("ru.json").read_text(encoding="utf-8")
    data = json.loads(raw)
    if not isinstance(data, dict):
        msg = "limits catalog must be a JSON object"
        raise TypeError(msg)
    return LimitsCatalog(
        user_quota=_require_template(data, "user_quota"),
        service_budget=_require_template(data, "service_budget"),
    )


def _require_template(data: dict[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        msg = f"limits catalog {key} must be a non-empty string"
        raise TypeError(msg)
    if "{reset_time}" not in value:
        msg = f"limits catalog {key} must contain {{reset_time}}"
        raise TypeError(msg)
    return value
