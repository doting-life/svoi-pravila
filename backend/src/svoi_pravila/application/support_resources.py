"""Load versioned crisis-support resource lines (C0 catalog)."""

from __future__ import annotations

from importlib import resources

from svoi_pravila.domain.safety import load_data_lines

_SAFETY_PACKAGE = "svoi_pravila.domain.safety"


def load_support_resources(*, locale: str = "ru", version: str = "v1") -> tuple[str, ...]:
    """Load support-resource lines from the versioned package resource."""
    resource = f"support_resources/{locale}/{version}.txt"
    raw = resources.files(_SAFETY_PACKAGE).joinpath(resource).read_text(encoding="utf-8")
    return load_data_lines(raw)
