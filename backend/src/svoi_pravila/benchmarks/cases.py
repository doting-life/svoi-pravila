"""Load and validate synthetic LLM benchmark cases."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

from svoi_pravila.application.ports.generation import (
    DecodeRequest,
    RewriteRequest,
    RuleContext,
    SuggestRuleRequest,
)
from svoi_pravila.domain.enums import RelationshipKind, RewriteGoal, RuleCategory

JSONL_OPERATIONS = ("rewrite", "decode", "suggest_rule")
RUN_OPERATIONS = ("rewrite", "decode_stream", "suggest_rule")
JsonlOperation = Literal["rewrite", "decode", "suggest_rule"]
RunOperation = Literal["rewrite", "decode_stream", "suggest_rule"]
ExpectedSafety = Literal["ok", "crisis"]
ExpectedVerdict = Literal["ok", "none"]


@dataclass(frozen=True, slots=True)
class BenchCase:
    """One validated synthetic benchmark case."""

    id: str
    operation: JsonlOperation
    expected_safety: ExpectedSafety
    smoke: bool = False
    rewrite: RewriteRequest | None = None
    decode: DecodeRequest | None = None
    suggest_rule: SuggestRuleRequest | None = None
    expected_verdict: ExpectedVerdict | None = None
    expected_category: RuleCategory | None = None


def _rules(raw: object) -> tuple[RuleContext, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        msg = "rules must be a list"
        raise TypeError(msg)
    rules: list[RuleContext] = []
    for item in raw:
        if not isinstance(item, dict):
            msg = "each rule must be an object"
            raise TypeError(msg)
        rules.append(
            RuleContext(
                category=RuleCategory(str(item["category"])),
                text=str(item["text"]),
                effective_since=datetime.fromisoformat(str(item["effective_since"])),
            )
        )
    return tuple(rules)


def parse_case(raw: object) -> BenchCase:
    """Validate one JSONL object into a BenchCase. Raises ValueError on bad data."""
    if not isinstance(raw, dict):
        msg = "case must be a JSON object"
        raise TypeError(msg)
    ident = str(raw.get("id") or "")
    if not ident:
        msg = "case id is required"
        raise ValueError(msg)
    operation = raw.get("operation")
    if operation not in JSONL_OPERATIONS:
        msg = f"unknown operation {operation!r}"
        raise ValueError(msg)
    expected_safety = raw.get("expected_safety")
    if expected_safety not in ("ok", "crisis"):
        msg = f"expected_safety must be 'ok' or 'crisis', got {expected_safety!r}"
        raise ValueError(msg)
    relationship = RelationshipKind(str(raw["relationship"]))
    rules = _rules(raw.get("rules"))
    smoke = bool(raw.get("smoke", False))
    if operation == "rewrite":
        goal_raw = raw.get("goal")
        return BenchCase(
            id=ident,
            operation="rewrite",
            expected_safety=expected_safety,
            smoke=smoke,
            rewrite=RewriteRequest(
                draft=str(raw["draft"]),
                goal=None if goal_raw is None else RewriteGoal(str(goal_raw)),
                rules=rules,
                relationship=relationship,
                deadline_seconds=1.0,
            ),
        )
    if operation == "suggest_rule":
        verdict = raw.get("expected_verdict")
        if verdict not in ("ok", "none"):
            msg = f"expected_verdict must be 'ok' or 'none', got {verdict!r}"
            raise ValueError(msg)
        category_raw = raw.get("expected_category")
        category: RuleCategory | None
        if verdict == "ok":
            if category_raw is None:
                msg = "expected_category required when expected_verdict is ok"
                raise ValueError(msg)
            category = RuleCategory(str(category_raw))
        else:
            category = None
        return BenchCase(
            id=ident,
            operation="suggest_rule",
            expected_safety=expected_safety,
            smoke=smoke,
            suggest_rule=SuggestRuleRequest(
                incoming=str(raw["incoming"]),
                rules=rules,
                relationship=relationship,
                deadline_seconds=1.0,
            ),
            expected_verdict=verdict,
            expected_category=category,
        )
    return BenchCase(
        id=ident,
        operation="decode",
        expected_safety=expected_safety,
        smoke=smoke,
        decode=DecodeRequest(
            incoming=str(raw["incoming"]),
            rules=rules,
            relationship=relationship,
            deadline_seconds=1.0,
        ),
    )


def load_cases(path: Path) -> list[BenchCase]:
    """Load and validate the synthetic JSONL dataset."""
    cases: list[BenchCase] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            cases.append(parse_case(json.loads(line)))
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            msg = f"{path}:{line_no}: invalid case: {exc}"
            raise ValueError(msg) from exc
    if not cases:
        msg = f"{path}: dataset is empty"
        raise ValueError(msg)
    return cases


def cases_for_operation(cases: list[BenchCase], operation: RunOperation) -> list[BenchCase]:
    """Return dataset rows applicable to a benchmark operation."""
    if operation == "decode_stream":
        return [case for case in cases if case.operation == "decode"]
    return [case for case in cases if case.operation == operation]


def filter_cases(
    cases: list[BenchCase],
    *,
    smoke: bool = False,
    case_ids: tuple[str, ...] | None = None,
) -> list[BenchCase]:
    """Apply smoke / id filters to a loaded dataset."""
    selected = cases
    if case_ids is not None:
        wanted = set(case_ids)
        selected = [case for case in selected if case.id in wanted]
        missing = wanted - {case.id for case in selected}
        if missing:
            msg = f"unknown case ids: {', '.join(sorted(missing))}"
            raise ValueError(msg)
    if smoke:
        selected = [case for case in selected if case.smoke]
    return selected


def with_deadline(case: BenchCase, deadline: float) -> BenchCase:
    """Return a copy of the case with the per-call deadline applied."""
    if case.operation == "rewrite" and case.rewrite is not None:
        req = case.rewrite
        return BenchCase(
            id=case.id,
            operation=case.operation,
            expected_safety=case.expected_safety,
            smoke=case.smoke,
            rewrite=RewriteRequest(
                draft=req.draft,
                goal=req.goal,
                rules=req.rules,
                relationship=req.relationship,
                deadline_seconds=deadline,
            ),
            expected_verdict=case.expected_verdict,
            expected_category=case.expected_category,
        )
    if case.operation == "suggest_rule" and case.suggest_rule is not None:
        req_s = case.suggest_rule
        return BenchCase(
            id=case.id,
            operation=case.operation,
            expected_safety=case.expected_safety,
            smoke=case.smoke,
            suggest_rule=SuggestRuleRequest(
                incoming=req_s.incoming,
                rules=req_s.rules,
                relationship=req_s.relationship,
                deadline_seconds=deadline,
            ),
            expected_verdict=case.expected_verdict,
            expected_category=case.expected_category,
        )
    req_d = case.decode
    if req_d is None:
        msg = "decode case missing request"
        raise ValueError(msg)
    return BenchCase(
        id=case.id,
        operation=case.operation,
        expected_safety=case.expected_safety,
        smoke=case.smoke,
        decode=DecodeRequest(
            incoming=req_d.incoming,
            rules=req_d.rules,
            relationship=req_d.relationship,
            deadline_seconds=deadline,
        ),
        expected_verdict=case.expected_verdict,
        expected_category=case.expected_category,
    )
