"""Load and validate synthetic safety-eval cases."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

from svoi_pravila.application.ports.generation import (
    DecodeRequest,
    HelpSayIntent,
    HelpSayRequest,
    RuleContext,
    SoftenRequest,
)
from svoi_pravila.domain.enums import RelationshipKind, RuleCategory

RunOperation = Literal["soften", "help_say", "decode_stream"]
EvalCategory = Literal[
    "ordinary_conflict",
    "heated",
    "mild_distress",
    "hyperbole",
    "explicit_crisis",
    "manipulation",
    "prompt_injection",
]
ExpectedVerdict = Literal["ok", "crisis", "refuse_manipulation"]
OPERATIONS: tuple[RunOperation, ...] = ("soften", "help_say", "decode_stream")
CATEGORIES: tuple[EvalCategory, ...] = (
    "ordinary_conflict",
    "heated",
    "mild_distress",
    "hyperbole",
    "explicit_crisis",
    "manipulation",
    "prompt_injection",
)


@dataclass(frozen=True, slots=True)
class EvalCase:
    """One synthetic safety-eval case (no real names or chats)."""

    id: str
    operation: RunOperation
    category: EvalCategory
    expected: ExpectedVerdict
    smoke: bool = False
    soften: SoftenRequest | None = None
    help_say: HelpSayRequest | None = None
    decode: DecodeRequest | None = None

    def screen_text(self) -> str:
        """User-supplied text the crisis screen inspects."""
        if self.soften is not None:
            return self.soften.draft
        if self.help_say is not None:
            return self.help_say.details
        if self.decode is not None:
            return self.decode.incoming
        msg = f"case {self.id} has no request payload"
        raise ValueError(msg)


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


def parse_case(raw: object) -> EvalCase:
    """Validate one JSONL object into an EvalCase."""
    if not isinstance(raw, dict):
        msg = "case must be a JSON object"
        raise TypeError(msg)
    ident = str(raw.get("id") or "")
    if not ident:
        msg = "case id is required"
        raise ValueError(msg)
    operation = raw.get("operation")
    if operation not in OPERATIONS:
        msg = f"unknown operation {operation!r}"
        raise ValueError(msg)
    category = raw.get("category")
    if category not in CATEGORIES:
        msg = f"unknown category {category!r}"
        raise ValueError(msg)
    expected = raw.get("expected")
    if expected not in ("ok", "crisis", "refuse_manipulation"):
        msg = f"expected must be a safety verdict, got {expected!r}"
        raise ValueError(msg)
    relationship = RelationshipKind(str(raw.get("relationship", "other")))
    rules = _rules(raw.get("rules"))
    smoke = bool(raw.get("smoke", False))
    if operation == "soften":
        return EvalCase(
            id=ident,
            operation="soften",
            category=category,
            expected=expected,
            smoke=smoke,
            soften=SoftenRequest(
                draft=str(raw["draft"]),
                rules=rules,
                relationship=relationship,
                deadline_seconds=1.0,
            ),
        )
    if operation == "help_say":
        return EvalCase(
            id=ident,
            operation="help_say",
            category=category,
            expected=expected,
            smoke=smoke,
            help_say=HelpSayRequest(
                intent=HelpSayIntent(str(raw["intent"])),
                details=str(raw["details"]),
                rules=rules,
                relationship=relationship,
                deadline_seconds=1.0,
            ),
        )
    return EvalCase(
        id=ident,
        operation="decode_stream",
        category=category,
        expected=expected,
        smoke=smoke,
        decode=DecodeRequest(
            incoming=str(raw["incoming"]),
            rules=rules,
            relationship=relationship,
            deadline_seconds=1.0,
        ),
    )


def load_cases(path: Path) -> list[EvalCase]:
    """Load and validate the synthetic JSONL dataset."""
    cases: list[EvalCase] = []
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


def cases_for_operation(cases: list[EvalCase], operation: RunOperation) -> list[EvalCase]:
    """Return rows for one eval operation."""
    return [case for case in cases if case.operation == operation]


def filter_cases(
    cases: list[EvalCase],
    *,
    smoke: bool = False,
    case_ids: tuple[str, ...] | None = None,
) -> list[EvalCase]:
    """Apply smoke / id filters."""
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


def with_deadline(case: EvalCase, deadline: float) -> EvalCase:
    """Copy the case with the per-call deadline applied."""
    if case.soften is not None:
        req = case.soften
        return EvalCase(
            id=case.id,
            operation=case.operation,
            category=case.category,
            expected=case.expected,
            smoke=case.smoke,
            soften=SoftenRequest(
                draft=req.draft,
                rules=req.rules,
                relationship=req.relationship,
                deadline_seconds=deadline,
            ),
        )
    if case.help_say is not None:
        help_req = case.help_say
        return EvalCase(
            id=case.id,
            operation=case.operation,
            category=case.category,
            expected=case.expected,
            smoke=case.smoke,
            help_say=HelpSayRequest(
                intent=help_req.intent,
                details=help_req.details,
                rules=help_req.rules,
                relationship=help_req.relationship,
                deadline_seconds=deadline,
            ),
        )
    if case.decode is None:
        msg = f"case {case.id} has no request payload"
        raise ValueError(msg)
    req_d = case.decode
    return EvalCase(
        id=case.id,
        operation=case.operation,
        category=case.category,
        expected=case.expected,
        smoke=case.smoke,
        decode=DecodeRequest(
            incoming=req_d.incoming,
            rules=req_d.rules,
            relationship=req_d.relationship,
            deadline_seconds=deadline,
        ),
    )
