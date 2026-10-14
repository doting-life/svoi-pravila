"""Unit tests for prompt rendering and marker allocation."""

from __future__ import annotations

import pytest

from svoi_pravila.adapters.llm.gigachat.boundaries import allocate_token
from svoi_pravila.adapters.llm.gigachat.prompt_compose import compose_rewrite_template
from svoi_pravila.adapters.llm.registry import load_prompt, render_system_prompt
from svoi_pravila.application.errors import InvalidGenerationOutput


@pytest.mark.unit
def test_render_rewrite_prompt() -> None:
    template = compose_rewrite_template(has_rules=True)
    _, version = load_prompt("rewrite", "v3")
    assert version == "rewrite@v3"
    rendered = render_system_prompt(template, boundary_marker="SPBOUND_ABC")
    assert "SPBOUND_ABC" in rendered
    assert "{{BOUNDARY_MARKER}}" not in rendered
    assert "refuse_manipulation" in template
    assert "applied_rule_indexes" in template


@pytest.mark.unit
def test_composed_no_rules_prompt_omits_indexes() -> None:
    template = compose_rewrite_template(has_rules=False)
    assert "applied_rule_indexes" not in template
    assert "refuse_manipulation" in template


@pytest.mark.unit
def test_render_decode_analysis_prompt() -> None:
    template, version = load_prompt("decode_analysis", "v1")
    assert version == "decode_analysis@v1"
    rendered = render_system_prompt(template, boundary_marker="SPBOUND_ANALYSIS")
    assert "SPBOUND_ANALYSIS" in rendered
    assert "{{BOUNDARY_MARKER}}" not in rendered
    assert "STREAM_SEPARATOR" not in template


@pytest.mark.unit
def test_render_decode_v4_prompt() -> None:
    template, version = load_prompt("decode", "v4")
    assert version == "decode@v4"
    rendered = render_system_prompt(template, boundary_marker="SPBOUND_DECODE")
    assert "SPBOUND_DECODE" in rendered
    assert "{{BOUNDARY_MARKER}}" not in rendered
    assert "STREAM_SEPARATOR" not in template
    assert "analysis:" in rendered


@pytest.mark.unit
def test_allocate_token_exhausted() -> None:
    with pytest.raises(InvalidGenerationOutput):
        allocate_token(["has SPBOUND_SAME"], factory=lambda: "SPBOUND_SAME")
