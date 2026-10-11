"""Compose soften/help_say system templates from one skeleton per scenario."""

from __future__ import annotations

from importlib import resources

from svoi_pravila.adapters.llm.registry import load_prompt_template


def _fragment(operation: str, name: str) -> str:
    package = f"svoi_pravila.adapters.llm.prompts.{operation}"
    return resources.files(package).joinpath(f"{name}.md").read_text(encoding="utf-8")


def _fill(
    skeleton: str,
    *,
    binding: str,
    refuse: str,
    crisis: str,
    json_close: str,
) -> str:
    return (
        skeleton.replace("{{BINDING_RULES}}", binding)
        .replace("{{REFUSE_LINE}}", refuse)
        .replace("{{CRISIS_LINE}}", crisis)
        .replace("{{JSON_CLOSE}}", json_close)
    )


def compose_soften_template(*, has_rules: bool) -> str:
    """Return the soften@v5 system template for the given rule shape."""
    skeleton, _version = load_prompt_template("soften", "v5")
    if has_rules:
        return _fill(
            skeleton,
            binding=_fragment("soften", "binding"),
            refuse=_fragment("soften", "refuse_with_rules"),
            crisis=_fragment("soften", "crisis_with_rules"),
            json_close=_fragment("soften", "json_close_with_rules"),
        )
    return _fill(
        skeleton,
        binding="",
        refuse=_fragment("soften", "refuse_no_rules"),
        crisis=_fragment("soften", "crisis_no_rules"),
        json_close=_fragment("soften", "json_close_no_rules"),
    )


def compose_help_say_template(*, has_rules: bool) -> str:
    """Return the help_say@v6 system template for the given rule shape."""
    skeleton, _version = load_prompt_template("help_say", "v6")
    if has_rules:
        return _fill(
            skeleton,
            binding=_fragment("help_say", "binding"),
            refuse=_fragment("help_say", "refuse_with_rules"),
            crisis=_fragment("help_say", "crisis_with_rules"),
            json_close=_fragment("help_say", "json_close_with_rules"),
        )
    return _fill(
        skeleton,
        binding="",
        refuse=_fragment("help_say", "refuse_no_rules"),
        crisis=_fragment("help_say", "crisis_no_rules"),
        json_close=_fragment("help_say", "json_close_no_rules"),
    )
