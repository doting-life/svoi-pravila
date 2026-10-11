"""Equivalence: composed templates match frozen with/without-rules fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest

from svoi_pravila.adapters.llm.gigachat.prompt_compose import (
    compose_help_say_template,
    compose_soften_template,
    empty_answer_json,
)
from svoi_pravila.adapters.llm.gigachat.schemas import (
    HelpSayOut,
    SafetyOut,
    SoftenOut,
    SoftenOutNoRules,
)

_FIXTURES = Path(__file__).resolve().parents[3] / "fixtures" / "llm" / "prompts"


@pytest.mark.unit
def test_empty_answer_json_matches_schema_shapes() -> None:
    assert empty_answer_json(SoftenOutNoRules, safety=SafetyOut.CRISIS) == (
        '{"gentle":"","balanced":"","firm":"","safety":"crisis"}'
    )
    assert empty_answer_json(SoftenOut, safety=SafetyOut.REFUSE_MANIPULATION) == (
        '{"gentle":"","balanced":"","firm":"","applied_rule_indexes":[],'
        '"safety":"refuse_manipulation"}'
    )
    assert empty_answer_json(HelpSayOut, safety=SafetyOut.CRISIS) == (
        '{"gentle":"","balanced":"","firm":"","applied_rule_indexes":[],"safety":"crisis"}'
    )


@pytest.mark.unit
def test_compose_soften_matches_fixtures() -> None:
    rules_fixture = (_FIXTURES / "soften_v5_rules.md").read_text(encoding="utf-8")
    no_rules = (_FIXTURES / "soften_v5_no_rules.md").read_text(encoding="utf-8")
    assert compose_soften_template(has_rules=True) == rules_fixture
    assert compose_soften_template(has_rules=False) == no_rules
    assert "applied_rule_indexes" not in compose_soften_template(has_rules=False)
    assert "{{EMPTY_REFUSE_JSON}}" not in rules_fixture
    assert "{{REFUSE_EXTRA}}" not in rules_fixture


@pytest.mark.unit
def test_compose_help_say_matches_fixtures() -> None:
    rules_fixture = (_FIXTURES / "help_say_v6_rules.md").read_text(encoding="utf-8")
    no_rules = (_FIXTURES / "help_say_v6_no_rules.md").read_text(encoding="utf-8")
    assert compose_help_say_template(has_rules=True) == rules_fixture
    assert compose_help_say_template(has_rules=False) == no_rules
    assert "applied_rule_indexes" not in compose_help_say_template(has_rules=False)
    assert "Не ставь индексы правил." in rules_fixture
    assert "Не ставь индексы правил." not in no_rules
