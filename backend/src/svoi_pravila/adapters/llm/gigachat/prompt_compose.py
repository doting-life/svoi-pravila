"""Compose the rewrite system template from one skeleton."""

from __future__ import annotations

import json
from dataclasses import dataclass
from importlib import resources

from pydantic import BaseModel

from svoi_pravila.adapters.llm.gigachat.schemas import (
    RewriteOut,
    RewriteOutNoRules,
    SafetyOut,
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
        "check": "",
        "softer": "",
        "firmer": "",
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


def compose_rewrite_template(*, has_rules: bool) -> str:
    """Return the rewrite@v1 system template for the given rule shape."""
    skeleton, _version = load_prompt_template("rewrite", "v1")
    schema: type[BaseModel] = RewriteOut if has_rules else RewriteOutNoRules
    return _fill(
        skeleton,
        _ComposeParts(
            binding=_fragment("rewrite", "binding") if has_rules else "",
            refuse_json=empty_answer_json(schema, safety=SafetyOut.REFUSE_MANIPULATION),
            crisis_json=empty_answer_json(schema, safety=SafetyOut.CRISIS),
            refuse_extra=_fragment("rewrite", "refuse_extra") if has_rules else "",
            json_close=_fragment(
                "rewrite", "json_close_rules" if has_rules else "json_close_no_rules"
            ),
        ),
    )
