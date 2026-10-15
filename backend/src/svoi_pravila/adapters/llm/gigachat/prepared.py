"""Rendered prompt messages for one GigaChat call."""

from __future__ import annotations

from dataclasses import dataclass

from svoi_pravila.adapters.llm.gigachat.boundaries import (
    allocate_boundary_marker,
    wrap_untrusted_payload,
)
from svoi_pravila.adapters.llm.gigachat.prompt_compose import compose_rewrite_template
from svoi_pravila.adapters.llm.gigachat.validation import rules_text, untrusted_texts
from svoi_pravila.adapters.llm.registry import load_prompt_template, render_system_prompt
from svoi_pravila.application.ports.generation import (
    DecodeRequest,
    RewriteRequest,
    SuggestRuleRequest,
)
from svoi_pravila.application.ports.rewrite_judge import JudgeRequest


@dataclass(frozen=True, slots=True)
class PreparedMessages:
    """Rendered system/user messages and prompt metadata for one call.

    ``untrusted`` holds the untrusted texts the boundary marker was allocated against.
    """

    system: str
    user: str
    prompt_version: str
    boundary_marker: str
    untrusted: tuple[str, ...]


def prepare_rewrite(request: RewriteRequest) -> PreparedMessages:
    """Build rendered system/user messages for rewrite."""
    goal = "none" if request.goal is None else request.goal.value
    untrusted = untrusted_texts(
        request.draft, goal, request.relationship.value, rules=request.rules
    )
    marker = allocate_boundary_marker(untrusted)
    template = compose_rewrite_template(has_rules=bool(request.rules))
    prompt_version = "rewrite@v2"
    system = render_system_prompt(template, boundary_marker=marker)
    user = wrap_untrusted_payload(
        marker,
        (
            ("draft", request.draft),
            ("goal", goal),
            ("rules", rules_text(request.rules)),
            ("relationship", request.relationship.value),
        ),
    )
    return PreparedMessages(
        system=system,
        user=user,
        prompt_version=prompt_version,
        boundary_marker=marker,
        untrusted=untrusted,
    )


def prepare_decode(request: DecodeRequest, *, analysis: str) -> PreparedMessages:
    """Build messages for structured decode phase B (prior analysis required)."""
    untrusted = untrusted_texts(
        request.incoming,
        request.relationship.value,
        analysis,
        rules=request.rules,
    )
    marker = allocate_boundary_marker(untrusted)
    template, prompt_version = load_prompt_template("decode", "v4")
    system = render_system_prompt(template, boundary_marker=marker)
    user = wrap_untrusted_payload(
        marker,
        (
            ("incoming", request.incoming),
            ("analysis", analysis),
            ("rules", rules_text(request.rules)),
            ("relationship", request.relationship.value),
        ),
    )
    return PreparedMessages(
        system=system,
        user=user,
        prompt_version=prompt_version,
        boundary_marker=marker,
        untrusted=untrusted,
    )


def prepare_decode_analysis(request: DecodeRequest) -> PreparedMessages:
    """Build messages for phase-A plain-text analysis stream."""
    untrusted = untrusted_texts(request.incoming, request.relationship.value, rules=request.rules)
    marker = allocate_boundary_marker(untrusted)
    template, prompt_version = load_prompt_template("decode_analysis", "v1")
    system = render_system_prompt(template, boundary_marker=marker)
    user = wrap_untrusted_payload(
        marker,
        (
            ("incoming", request.incoming),
            ("rules", rules_text(request.rules)),
            ("relationship", request.relationship.value),
        ),
    )
    return PreparedMessages(
        system=system,
        user=user,
        prompt_version=prompt_version,
        boundary_marker=marker,
        untrusted=untrusted,
    )


def prepare_suggest_rule(request: SuggestRuleRequest) -> PreparedMessages:
    """Build rendered system/user messages for suggest_rule."""
    untrusted = untrusted_texts(request.incoming, request.relationship.value, rules=request.rules)
    marker = allocate_boundary_marker(untrusted)
    template, prompt_version = load_prompt_template("suggest_rule", "v1")
    system = render_system_prompt(template, boundary_marker=marker)
    user = wrap_untrusted_payload(
        marker,
        (
            ("incoming", request.incoming),
            ("rules", rules_text(request.rules)),
            ("relationship", request.relationship.value),
        ),
    )
    return PreparedMessages(
        system=system,
        user=user,
        prompt_version=prompt_version,
        boundary_marker=marker,
        untrusted=untrusted,
    )


def prepare_judge(request: JudgeRequest) -> PreparedMessages:
    """Build rendered system/user messages for the eval clean judge.

    The text is model output and is delimited like every other untrusted field.
    """
    untrusted = untrusted_texts(request.text)
    marker = allocate_boundary_marker(untrusted)
    template, prompt_version = load_prompt_template("judge_clean", "v2")
    system = render_system_prompt(template, boundary_marker=marker)
    user = wrap_untrusted_payload(marker, (("text", request.text),))
    return PreparedMessages(
        system=system,
        user=user,
        prompt_version=prompt_version,
        boundary_marker=marker,
        untrusted=untrusted,
    )
