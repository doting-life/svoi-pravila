"""Load versioned crisis-support resource lines and related C0 safety copy."""

from __future__ import annotations

from importlib import resources

from svoi_pravila.domain.safety import load_data_lines

_SAFETY_PACKAGE = "svoi_pravila.domain.safety"


def load_support_resources(*, locale: str = "ru", version: str = "v1") -> tuple[str, ...]:
    """Load support-resource lines from the versioned package resource."""
    resource = f"support_resources/{locale}/{version}.txt"
    raw = resources.files(_SAFETY_PACKAGE).joinpath(resource).read_text(encoding="utf-8")
    return load_data_lines(raw)


def load_crisis_lead(*, locale: str = "ru", version: str = "v1") -> str:
    """Load the crisis lead paragraph shown before support-resource contacts."""
    resource = f"crisis_copy/{locale}/{version}.txt"
    raw = resources.files(_SAFETY_PACKAGE).joinpath(resource).read_text(encoding="utf-8")
    lines = load_data_lines(raw)
    if not lines:
        msg = f"crisis_copy/{locale}/{version}.txt has no lead text"
        raise ValueError(msg)
    return "\n".join(lines)


def load_applied_rule_template(*, locale: str = "ru", version: str = "v1") -> str:
    """Load the decode applied-rule citation template (``{date}``, ``{text}``)."""
    resource = f"decode_copy/{locale}/{version}.txt"
    raw = resources.files(_SAFETY_PACKAGE).joinpath(resource).read_text(encoding="utf-8")
    lines = load_data_lines(raw)
    if len(lines) != 1:
        msg = f"decode_copy/{locale}/{version}.txt must contain exactly one template line"
        raise ValueError(msg)
    return lines[0]
