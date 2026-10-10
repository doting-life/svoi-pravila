"""Map validated generation indexes onto the rule context that was passed in."""

from __future__ import annotations

from svoi_pravila.application.ports.generation import AppliedRuleView, RuleContext


def applied_rule_views(
    rules: tuple[RuleContext, ...],
    indexes: tuple[int, ...],
) -> tuple[AppliedRuleView, ...]:
    """Keep indexes that fall inside ``rules``; skip anything else."""
    views: list[AppliedRuleView] = []
    bound = len(rules)
    for index in indexes:
        if 0 <= index < bound:
            rule = rules[index]
            views.append(
                AppliedRuleView(
                    index=index,
                    category=rule.category,
                    text=rule.text,
                    effective_since=rule.effective_since,
                )
            )
    return tuple(views)
