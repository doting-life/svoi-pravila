"""Equivalence: composed templates match frozen with/without-rules fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest

from svoi_pravila.adapters.llm.gigachat.prompt_compose import (
    compose_help_say_template,
    compose_soften_template,
)

_FIXTURES = Path(__file__).resolve().parents[3] / "fixtures" / "llm" / "prompts"


@pytest.mark.unit
def test_compose_soften_matches_fixtures() -> None:
    with_rules = (_FIXTURES / "soften_v5_with_rules.md").read_text(encoding="utf-8")
    no_rules = (_FIXTURES / "soften_v5_no_rules.md").read_text(encoding="utf-8")
    assert compose_soften_template(has_rules=True) == with_rules
    assert compose_soften_template(has_rules=False) == no_rules
    assert "applied_rule_indexes" not in compose_soften_template(has_rules=False)


@pytest.mark.unit
def test_compose_help_say_matches_fixtures() -> None:
    with_rules = (_FIXTURES / "help_say_v6_with_rules.md").read_text(encoding="utf-8")
    no_rules = (_FIXTURES / "help_say_v6_no_rules.md").read_text(encoding="utf-8")
    assert compose_help_say_template(has_rules=True) == with_rules
    assert compose_help_say_template(has_rules=False) == no_rules
    assert "applied_rule_indexes" not in compose_help_say_template(has_rules=False)
