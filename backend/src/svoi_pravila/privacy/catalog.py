"""Typed loader for the shared privacy localization catalog."""

from __future__ import annotations

import json
from dataclasses import dataclass
from importlib import resources
from typing import Any


@dataclass(frozen=True, slots=True)
class PrivacyExportTexts:
    """Export disclosure copy and section display names."""

    description: str
    sections: dict[str, str]


@dataclass(frozen=True, slots=True)
class PrivacyActionTexts:
    """Blurb and confirm copy for revoke or delete."""

    description: str
    confirm: str


@dataclass(frozen=True, slots=True)
class PrivacyCatalog:
    """User-facing privacy texts shared by bot and mini-app API (C0)."""

    export: PrivacyExportTexts
    revoke: PrivacyActionTexts
    delete: PrivacyActionTexts
    leave_pair: PrivacyActionTexts


def load_privacy_catalog() -> PrivacyCatalog:
    """Load ``privacy/ru.json``; malformed or incomplete catalogs fail at startup."""
    raw = resources.files(__package__).joinpath("ru.json").read_text(encoding="utf-8")
    data = json.loads(raw)
    if not isinstance(data, dict):
        msg = "privacy catalog must be a JSON object"
        raise TypeError(msg)
    return PrivacyCatalog(
        export=_load_export(data.get("export")),
        revoke=_load_action(data.get("revoke"), label="revoke"),
        delete=_load_action(data.get("delete"), label="delete"),
        leave_pair=_load_action(data.get("leave_pair"), label="leave_pair"),
    )


def _load_export(value: Any) -> PrivacyExportTexts:
    if not isinstance(value, dict):
        msg = "privacy catalog export must be an object"
        raise TypeError(msg)
    description = value.get("description")
    sections = value.get("sections")
    if not isinstance(description, str) or not description:
        msg = "privacy catalog export.description must be a non-empty string"
        raise TypeError(msg)
    if not isinstance(sections, dict) or not sections:
        msg = "privacy catalog export.sections must be a non-empty object"
        raise TypeError(msg)
    mapped: dict[str, str] = {}
    for key, name in sections.items():
        if not isinstance(key, str) or not isinstance(name, str) or not name:
            msg = "privacy catalog export.sections entries must be non-empty strings"
            raise TypeError(msg)
        mapped[key] = name
    return PrivacyExportTexts(description=description, sections=mapped)


def _load_action(value: Any, *, label: str) -> PrivacyActionTexts:
    if not isinstance(value, dict):
        msg = f"privacy catalog {label} must be an object"
        raise TypeError(msg)
    description = value.get("description")
    confirm = value.get("confirm")
    if not isinstance(description, str) or not description:
        msg = f"privacy catalog {label}.description must be a non-empty string"
        raise TypeError(msg)
    if not isinstance(confirm, str) or not confirm:
        msg = f"privacy catalog {label}.confirm must be a non-empty string"
        raise TypeError(msg)
    return PrivacyActionTexts(description=description, confirm=confirm)
