"""Interview cases for the rewrite eval: a committed synthetic file plus an external one."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from svoi_pravila.domain.enums import RelationshipKind, RewriteGoal


@dataclass(frozen=True, slots=True)
class RewriteCase:
    """One draft run through ``rewrite`` (every kind comes back from a single call)."""

    id: str
    draft: str
    goal: RewriteGoal | None = None
    relationship: RelationshipKind = RelationshipKind.OTHER


def parse_rewrite_case(raw: object) -> RewriteCase:
    """Validate one JSONL object into a ``RewriteCase``."""
    if not isinstance(raw, dict):
        msg = "case must be a JSON object"
        raise TypeError(msg)
    ident = str(raw.get("id") or "")
    if not ident:
        msg = "case id is required"
        raise ValueError(msg)
    goal_raw = raw.get("goal")
    return RewriteCase(
        id=ident,
        draft=str(raw["draft"]),
        goal=None if goal_raw is None else RewriteGoal(str(goal_raw)),
        relationship=RelationshipKind(str(raw.get("relationship", "other"))),
    )


def load_rewrite_cases(path: Path) -> list[RewriteCase]:
    """Load a JSONL file of rewrite cases; an empty file is an error."""
    cases: list[RewriteCase] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            cases.append(parse_rewrite_case(json.loads(line)))
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            msg = f"{path}:{line_no}: invalid case: {exc}"
            raise ValueError(msg) from exc
    if not cases:
        msg = f"{path}: dataset is empty"
        raise ValueError(msg)
    return cases


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
