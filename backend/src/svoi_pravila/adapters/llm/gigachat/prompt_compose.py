"""Compose soften/help_say system templates from one skeleton per scenario."""

from __future__ import annotations

import json
from dataclasses import dataclass
from importlib import resources

from pydantic import BaseModel

from svoi_pravila.adapters.llm.gigachat.schemas import (
    HelpSayOut,
    HelpSayOutNoRules,
    SafetyOut,
    SoftenOut,
    SoftenOutNoRules,
)
from svoi_pravila.adapters.llm.registry import load_prompt_template


@dataclass(frozen=True, slots=True)
class _ComposeParts:
    binding: str
    refuse_json: str
    crisis_json: str
    refuse_extra: str
    json_close: str


def _fragment(operation: str, name: str) -> str:
    package = f"svoi_pravila.adapters.llm.prompts.{operation}"
    return resources.files(package).joinpath(f"{name}.md").read_text(encoding="utf-8")


def empty_answer_json[T: BaseModel](schema: type[T], *, safety: SafetyOut) -> str:
    """Compact empty-slot JSON example from the structured-output schema."""
    data: dict[str, object] = {
        "gentle": "",
        "balanced": "",
        "firm": "",
        "safety": safety,
    }
    if "applied_rule_indexes" in schema.model_fields:
        data["applied_rule_indexes"] = []
    parsed = schema.model_validate(data)
    return json.dumps(parsed.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":"))


def _fill(skeleton: str, parts: _ComposeParts) -> str:
    return (
        skeleton.replace("{{BINDING_RULES}}", parts.binding)
        .replace("{{EMPTY_REFUSE_JSON}}", parts.refuse_json)
        .replace("{{EMPTY_CRISIS_JSON}}", parts.crisis_json)
        .replace("{{REFUSE_EXTRA}}", parts.refuse_extra)
        .replace("{{JSON_CLOSE}}", parts.json_close)
    )


def compose_soften_template(*, has_rules: bool) -> str:
    """Return the soften@v5 system template for the given rule shape."""
    skeleton, _version = load_prompt_template("soften", "v5")
    schema: type[BaseModel] = SoftenOut if has_rules else SoftenOutNoRules
    if has_rules:
        return _fill(
            skeleton,
            _ComposeParts(
                binding=_fragment("soften", "binding"),
                refuse_json=empty_answer_json(schema, safety=SafetyOut.REFUSE_MANIPULATION),
                crisis_json=empty_answer_json(schema, safety=SafetyOut.CRISIS),
                refuse_extra="",
                json_close=_fragment("soften", "json_close_rules"),
            ),
        )
    return _fill(
        skeleton,
        _ComposeParts(
            binding="",
            refuse_json=empty_answer_json(schema, safety=SafetyOut.REFUSE_MANIPULATION),
            crisis_json=empty_answer_json(schema, safety=SafetyOut.CRISIS),
            refuse_extra="",
            json_close=_fragment("soften", "json_close_no_rules"),
        ),
    )


def compose_help_say_template(*, has_rules: bool) -> str:
    """Return the help_say@v6 system template for the given rule shape."""
    skeleton, _version = load_prompt_template("help_say", "v6")
    schema: type[BaseModel] = HelpSayOut if has_rules else HelpSayOutNoRules
    refuse_extra = _fragment("help_say", "refuse_extra") if has_rules else ""
    if has_rules:
        return _fill(
            skeleton,
            _ComposeParts(
                binding=_fragment("help_say", "binding"),
                refuse_json=empty_answer_json(schema, safety=SafetyOut.REFUSE_MANIPULATION),
                crisis_json=empty_answer_json(schema, safety=SafetyOut.CRISIS),
                refuse_extra=refuse_extra,
                json_close=_fragment("help_say", "json_close_rules"),
            ),
        )
    return _fill(
        skeleton,
        _ComposeParts(
            binding="",
            refuse_json=empty_answer_json(schema, safety=SafetyOut.REFUSE_MANIPULATION),
            crisis_json=empty_answer_json(schema, safety=SafetyOut.CRISIS),
            refuse_extra="",
            json_close=_fragment("help_say", "json_close_no_rules"),
        ),
    )
