"""Cases for the rewrite eval: a committed synthetic file plus an external one."""

from __future__ import annotations

import json
from collections.abc import Collection
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from svoi_pravila.application.ports.generation import RuleContext
from svoi_pravila.domain.enums import RelationshipKind, RewriteGoal
from svoi_pravila.evals.cases import parse_rules


class RewriteCategory(StrEnum):
    """Which layer of the rewrite contract a case measures (ADR-0016 §7)."""

    BASE = "base"
    PROFANITY = "profanity"
    GOAL = "goal"
    RULE = "rule"
    LADDER = "ladder"


_FIELDS = frozenset(
    {
        "id",
        "category",
        "draft",
        "goal",
        "relationship",
        "rules",
        "check_expected",
        "forbidden_stems",
        "effect_pair",
        "smoke",
    }
)


@dataclass(frozen=True, slots=True)
class RewriteCase:
    """One draft run through ``rewrite`` (every kind comes back from a single call).

    ``check_expected`` lists the accepted corrected texts of the ``check`` variant; empty
    means the case has no reference and ``check`` is not compared. ``forbidden_stems`` are
    prohibitions and threats the draft does not contain; ``effect_pair`` links a case with
    a closing-ask rule to its twin without the rule; ``smoke`` marks the small fast subset.
    """

    id: str
    category: RewriteCategory
    draft: str
    goal: RewriteGoal | None = None
    relationship: RelationshipKind = RelationshipKind.OTHER
    rules: tuple[RuleContext, ...] = ()
    check_expected: tuple[str, ...] = ()
    forbidden_stems: tuple[str, ...] = ()
    effect_pair: str | None = None
    smoke: bool = False


def _string_list(raw: object, field_name: str) -> tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list) or not all(isinstance(item, str) and item.strip() for item in raw):
        msg = f"{field_name} must be a list of non-empty strings"
        raise TypeError(msg)
    return tuple(raw)


def _required_text(raw: dict[str, object], field_name: str) -> str:
    value = raw.get(field_name)
    if not isinstance(value, str) or not value.strip():
        msg = f"{field_name} must be a non-empty string"
        raise ValueError(msg)
    return value


def _optional_text(raw: dict[str, object], field_name: str) -> str | None:
    if field_name not in raw or raw[field_name] is None:
        return None
    return _required_text(raw, field_name)


def _smoke(raw: dict[str, object]) -> bool:
    value = raw.get("smoke", False)
    if not isinstance(value, bool):
        msg = "smoke must be true or false"
        raise TypeError(msg)
    return value


def parse_rewrite_case(raw: object) -> RewriteCase:
    """Validate one JSONL object into a ``RewriteCase``; unknown fields are an error."""
    if not isinstance(raw, dict):
        msg = "case must be a JSON object"
        raise TypeError(msg)
    unknown = sorted(set(raw) - _FIELDS)
    if unknown:
        msg = f"unknown fields: {', '.join(unknown)}"
        raise ValueError(msg)
    goal_raw = _optional_text(raw, "goal")
    category = RewriteCategory(_required_text(raw, "category"))
    goal = None if goal_raw is None else RewriteGoal(goal_raw)
    if category is RewriteCategory.GOAL and goal is None:
        msg = "goal cases require a goal"
        raise ValueError(msg)
    return RewriteCase(
        id=_required_text(raw, "id"),
        category=category,
        draft=_required_text(raw, "draft"),
        goal=goal,
        relationship=RelationshipKind(_optional_text(raw, "relationship") or "other"),
        rules=parse_rules(raw.get("rules")),
        check_expected=_string_list(raw.get("check_expected"), "check_expected"),
        forbidden_stems=_string_list(raw.get("forbidden_stems"), "forbidden_stems"),
        effect_pair=_optional_text(raw, "effect_pair"),
        smoke=_smoke(raw),
    )


def load_rewrite_cases(path: Path) -> list[RewriteCase]:
    """Load a JSONL file of rewrite cases; an empty file or a repeated id is an error."""
    cases: list[RewriteCase] = []
    seen: set[str] = set()
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            case = parse_rewrite_case(json.loads(line))
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            msg = f"{path}:{line_no}: invalid case: {exc}"
            raise ValueError(msg) from exc
        if case.id in seen:
            msg = f"{path}:{line_no}: duplicate case id: {case.id}"
            raise ValueError(msg)
        seen.add(case.id)
        cases.append(case)
    if not cases:
        msg = f"{path}: dataset is empty"
        raise ValueError(msg)
    return cases


def select_categories(
    cases: list[RewriteCase], categories: Collection[RewriteCategory]
) -> list[RewriteCase]:
    """Keep the cases of the given categories; no categories keeps every case."""
    if not categories:
        return cases
    selected = [case for case in cases if case.category in categories]
    if not selected:
        msg = "no case belongs to the selected categories"
        raise ValueError(msg)
    return selected


def merge_cases(base: list[RewriteCase], extra: list[RewriteCase]) -> list[RewriteCase]:
    """Append external cases to the committed ones; ids must stay unique."""
    merged = [*base, *extra]
    seen: set[str] = set()
    for case in merged:
        if case.id in seen:
            msg = f"duplicate case id: {case.id}"
            raise ValueError(msg)
        seen.add(case.id)
    return merged
