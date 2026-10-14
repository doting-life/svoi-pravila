"""Calibration items of the rewrite judge: a committed synthetic JSONL file (ADR-0016 §13)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from svoi_pravila.application.ports.generation import RuleContext
from svoi_pravila.application.ports.rewrite_judge import SlotVerdicts, Verdict
from svoi_pravila.domain.enums import RewriteGoal
from svoi_pravila.evals.cases import parse_rules

_FIELDS = frozenset({"id", "draft", "goal", "rules", "softer", "firmer", "expected"})
_SLOT_FIELDS = frozenset({"clean", "meaning_kept", "invented", "goal_done", "rule_done"})
_EXPECTED_FIELDS = frozenset({"softer", "firmer", "ladder_ok"})


@dataclass(frozen=True, slots=True)
class ExpectedVerdicts:
    """The approved labels of one item: eleven values, five per slot and ``ladder_ok``."""

    softer: SlotVerdicts
    firmer: SlotVerdicts
    ladder_ok: Verdict

    def __post_init__(self) -> None:
        if self.ladder_ok is Verdict.NA:
            msg = "ladder_ok is never na"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class JudgeCase:
    """One draft with two variants and the labels the judge is calibrated against."""

    id: str
    draft: str
    goal: RewriteGoal | None
    rules: tuple[RuleContext, ...]
    softer: str
    firmer: str
    expected: ExpectedVerdicts


def _text(raw: dict[str, object], field_name: str) -> str:
    value = raw.get(field_name)
    if not isinstance(value, str) or not value.strip():
        msg = f"{field_name} must be a non-empty string"
        raise ValueError(msg)
    return value


def _verdict(raw: object, field_name: str) -> Verdict:
    try:
        return Verdict(str(raw))
    except ValueError:
        msg = f"{field_name} must be one of yes, no, na"
        raise ValueError(msg) from None


def _object(raw: object, field_name: str, allowed: frozenset[str]) -> dict[str, object]:
    if not isinstance(raw, dict):
        msg = f"{field_name} must be a JSON object"
        raise TypeError(msg)
    unknown = set(raw) - allowed
    missing = allowed - set(raw)
    if unknown or missing:
        msg = f"{field_name}: unknown {sorted(unknown)}, missing {sorted(missing)}"
        raise ValueError(msg)
    return raw


def _slot(raw: object, name: str) -> SlotVerdicts:
    fields = _object(raw, f"expected.{name}", _SLOT_FIELDS)
    return SlotVerdicts(**{key: _verdict(fields[key], f"{name}.{key}") for key in _SLOT_FIELDS})


def _goal(raw: object) -> RewriteGoal | None:
    return None if raw is None else RewriteGoal(str(raw))


def parse_judge_case(raw: object) -> JudgeCase:
    """Validate one JSONL object into a JudgeCase; any other shape is an error."""
    fields = _object(raw, "item", _FIELDS)
    expected = _object(fields["expected"], "expected", _EXPECTED_FIELDS)
    goal = _goal(fields["goal"])
    rules = parse_rules(fields["rules"])
    case = JudgeCase(
        id=_text(fields, "id"),
        draft=_text(fields, "draft"),
        goal=goal,
        rules=rules,
        softer=_text(fields, "softer"),
        firmer=_text(fields, "firmer"),
        expected=ExpectedVerdicts(
            softer=_slot(expected["softer"], "softer"),
            firmer=_slot(expected["firmer"], "firmer"),
            ladder_ok=_verdict(expected["ladder_ok"], "ladder_ok"),
        ),
    )
    _check_not_applicable(case)
    return case


def _check_not_applicable(case: JudgeCase) -> None:
    """``goal_done`` is ``na`` exactly when there is no goal; no rules means ``rule_done`` na."""
    for slot in (case.expected.softer, case.expected.firmer):
        if (case.goal is None) != (slot.goal_done is Verdict.NA):
            msg = f"{case.id}: goal_done is na exactly when goal is null"
            raise ValueError(msg)
        if not case.rules and slot.rule_done is not Verdict.NA:
            msg = f"{case.id}: rule_done must be na without rules"
            raise ValueError(msg)


def load_judge_cases(path: Path) -> list[JudgeCase]:
    """Read the calibration items; ids are unique and the file is not empty."""
    cases: list[JudgeCase] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            cases.append(parse_judge_case(json.loads(line)))
        except (TypeError, ValueError, KeyError) as exc:
            msg = f"{path}:{number}: {exc}"
            raise ValueError(msg) from None
    ids = [case.id for case in cases]
    if not cases or len(set(ids)) != len(ids):
        msg = f"{path}: needs at least one item and unique ids"
        raise ValueError(msg)
    return cases
