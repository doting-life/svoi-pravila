"""Equivalence: composed templates match frozen with/without-rules fixtures."""

from __future__ import annotations

from importlib import resources
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
_V2_CHARACTERS = 5479
_MAX_GROWTH_OVER_V2 = 1.6


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
    rules_fixture = (_FIXTURES / "rewrite_v3_rules.md").read_text(encoding="utf-8")
    no_rules = (_FIXTURES / "rewrite_v3_no_rules.md").read_text(encoding="utf-8")
    assert compose_rewrite_template(has_rules=True) == rules_fixture
    assert compose_rewrite_template(has_rules=False) == no_rules
    assert "applied_rule_indexes" not in no_rules
    assert "applied_rule_indexes" in rules_fixture
    assert "{{EMPTY_REFUSE_JSON}}" not in rules_fixture
    assert "{{REFUSE_EXTRA}}" not in rules_fixture
    assert "{{BINDING_RULES}}" not in rules_fixture


@pytest.mark.unit
def test_rewrite_v3_skeleton_is_at_most_one_point_six_times_v2() -> None:
    skeleton = (
        resources.files("svoi_pravila.adapters.llm.prompts.rewrite")
        .joinpath("v3.md")
        .read_text(encoding="utf-8")
    )
    assert len(skeleton) <= _MAX_GROWTH_OVER_V2 * _V2_CHARACTERS
