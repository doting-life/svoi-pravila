"""Validated applied-rule index mapping."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from svoi_pravila.application.applied_rules import applied_rule_views
from svoi_pravila.application.ports.generation import RuleContext
from svoi_pravila.domain.enums import RuleCategory

_NOW = datetime(2026, 10, 3, 12, tzinfo=UTC)


def _rule(text: str) -> RuleContext:
    return RuleContext(category=RuleCategory.OTHER, text=text, effective_since=_NOW)


@pytest.mark.unit
def test_applied_rule_views_skips_out_of_range_and_keeps_order() -> None:
    rules = (_rule("a"), _rule("b"), _rule("c"))
    views = applied_rule_views(rules, (0, 9, 2, -1, 1, 0))
    assert tuple(view.text for view in views) == ("a", "c", "b", "a")
    assert tuple(view.index for view in views) == (0, 2, 1, 0)
    assert applied_rule_views((), (0,)) == ()
