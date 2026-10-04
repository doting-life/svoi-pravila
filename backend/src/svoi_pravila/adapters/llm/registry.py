"""Versioned system-prompt registry loaded from package resources."""

from __future__ import annotations

from importlib import resources

_BOUNDARY_PLACEHOLDER = "{{BOUNDARY_MARKER}}"


def load_prompt_template(operation: str, version: str = "v1") -> tuple[str, str]:
    """Return ``(template_text, prompt_version)`` for an operation.

    ``prompt_version`` is formatted as ``{operation}@{version}`` (e.g. ``soften@v1``).
    """
    package = f"svoi_pravila.adapters.llm.prompts.{operation}"
    resource = f"{version}.md"
    text = resources.files(package).joinpath(resource).read_text(encoding="utf-8")
    return text, f"{operation}@{version}"


def render_system_prompt(template: str, *, boundary_marker: str) -> str:
    """Render a prompt template with the per-request boundary marker."""
    return template.replace(_BOUNDARY_PLACEHOLDER, boundary_marker)


def load_prompt(operation: str, version: str = "v1") -> tuple[str, str]:
    """Backward-compatible load of an unrendered template and version string."""
    return load_prompt_template(operation, version)
