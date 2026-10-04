"""Unit tests for prompt rendering and marker allocation."""

from __future__ import annotations

import pytest

from svoi_pravila.adapters.llm.gigachat.boundaries import allocate_token
from svoi_pravila.adapters.llm.registry import load_prompt, render_system_prompt
from svoi_pravila.application.errors import InvalidGenerationOutput


@pytest.mark.unit
def test_render_system_prompt_ok() -> None:
    template, version = load_prompt("soften", "v3")
    assert version == "soften@v3"
    rendered = render_system_prompt(template, boundary_marker="SPBOUND_ABC")
    assert "SPBOUND_ABC" in rendered
    assert "{{BOUNDARY_MARKER}}" not in rendered


@pytest.mark.unit
def test_render_help_say_v2_prompt() -> None:
    template, version = load_prompt("help_say", "v3")
    assert version == "help_say@v3"
    rendered = render_system_prompt(template, boundary_marker="SPBOUND_HELP")
    assert "SPBOUND_HELP" in rendered
    assert "{{BOUNDARY_MARKER}}" not in rendered
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
def test_render_decode_v3_prompt() -> None:
    template, version = load_prompt("decode", "v3")
    assert version == "decode@v3"
    rendered = render_system_prompt(template, boundary_marker="SPBOUND_DECODE")
    assert "SPBOUND_DECODE" in rendered
    assert "{{BOUNDARY_MARKER}}" not in rendered
    assert "STREAM_SEPARATOR" not in template
    assert "analysis:" in rendered


@pytest.mark.unit
def test_allocate_token_exhausted() -> None:
    with pytest.raises(InvalidGenerationOutput):
        allocate_token(["has SPBOUND_SAME"], factory=lambda: "SPBOUND_SAME")
