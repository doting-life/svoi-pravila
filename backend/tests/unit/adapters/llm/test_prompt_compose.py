"""Equivalence: composed templates match frozen with/without-rules fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest

from svoi_pravila.adapters.llm.gigachat.prompt_compose import (
    compose_rewrite_template,
    empty_answer_json,
)
from svoi_pravila.adapters.llm.gigachat.schemas import (
    RewriteOut,
    RewriteOutNoRules,
    SafetyOut,
)

_FIXTURES = Path(__file__).resolve().parents[3] / "fixtures" / "llm" / "prompts"


@pytest.mark.unit
def test_empty_answer_json_matches_schema_shapes() -> None:
    assert empty_answer_json(RewriteOutNoRules, safety=SafetyOut.CRISIS) == (
        '{"check":"","softer":"","firmer":"","safety":"crisis"}'
    )
    assert empty_answer_json(RewriteOut, safety=SafetyOut.REFUSE_MANIPULATION) == (
        '{"check":"","softer":"","firmer":"","applied_rule_indexes":[],'
        '"safety":"refuse_manipulation"}'
    )


@pytest.mark.unit
def test_compose_rewrite_matches_fixtures() -> None:
    rules_fixture = (_FIXTURES / "rewrite_v1_rules.md").read_text(encoding="utf-8")
    no_rules = (_FIXTURES / "rewrite_v1_no_rules.md").read_text(encoding="utf-8")
    assert compose_rewrite_template(has_rules=True) == rules_fixture
    assert compose_rewrite_template(has_rules=False) == no_rules
    assert "applied_rule_indexes" not in no_rules
    assert "applied_rule_indexes" in rules_fixture
    assert "{{EMPTY_REFUSE_JSON}}" not in rules_fixture
    assert "{{REFUSE_EXTRA}}" not in rules_fixture
    assert "{{BINDING_RULES}}" not in rules_fixture
