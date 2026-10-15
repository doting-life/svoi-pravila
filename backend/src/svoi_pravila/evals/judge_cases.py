"""Calibration items of the rewrite judge: a committed synthetic JSONL file (ADR-0016 §13).

Labelling contract. Every label of ``evals/data/judge_calibration_v1.jsonl`` follows these
rules, which restate the judge prompt; a label that breaks one is a defect of the set:

* R1 ``meaning_kept`` is ``no`` only when the draft's action or position is lost, inverted or
  weakened: a refusal becomes "cannot", "maybe", "probably" or conditional; a demand becomes a
  wish or a suggestion; a boundary becomes a wish; regret becomes blame; the addressee or the
  subject changes (including the author's grammatical gender). Additions alone do not change
  meaning: insults go to ``clean``, new content to ``invented``, a broken goal to ``goal_done``.
* R2 ``invented`` is ``yes`` for new facts, motives ("on purpose", "you do not care about me"),
  conditions, deadlines, promises, threats, prohibitions, ultimatums and participants. It is not
  ``yes`` for softeners and hedges, a named feeling in place of a rude word, insults (they are
  ``clean``), the one sentence that performs the goal, or content a rule requires.
* R3 ``clean`` is ``no`` only for vulgar or obscene words in any form, insults and accusatory
  generalizations. Threats, taunts and coldness without such words are ``clean`` = ``yes``.
* R4 ``goal_done``: a hedged or conditional refusal is ``no``; a boundary as a wish or a
  question is ``no``; a boundary with an added threat is ``yes`` (the threat is ``invented``);
  ``reconnect`` with a prohibition, reproach or ultimatum is ``no``; ``admit_fault`` with an
  excuse or self-humiliation is ``no``.
* R5 ``rule_done`` is ``na`` when the rule's subject has nothing to do with the draft (and
  always when there are no rules).
* R6 ``ladder_ok`` is ``no`` when ``firmer`` has softeners or excuses or adds new pressure
  (a threat, an ultimatum, a jab), when ``softer`` is not warmer, or when the two differ only by
  punctuation. ``clean`` does not affect ``ladder_ok``. Only an eligible pair is measured: one
  whose every slot label is good (``clean`` yes, ``meaning_kept`` yes, ``invented`` no,
  ``goal_done`` and ``rule_done`` yes or ``na``). On every other pair the label stays in the file
  but takes no part in the calibration.

Every item has a ``source``: ``synthetic`` (written for the set) or ``eval_output`` (a real
``softer``/``firmer`` pair of a rewrite eval run, copied exactly and labelled by R1-R6; these
may repeat drafts of ``rewrite_v2.jsonl``).

Quotas are enforced by ``tests/unit/evals/test_judge_calibration_dataset.py``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from svoi_pravila.application.ports.generation import RuleContext
from svoi_pravila.application.ports.rewrite_judge import SlotVerdicts, Verdict
from svoi_pravila.domain.enums import RewriteGoal
from svoi_pravila.evals.cases import parse_rules

SOURCE_SYNTHETIC = "synthetic"
SOURCE_EVAL_OUTPUT = "eval_output"
SOURCES = (SOURCE_SYNTHETIC, SOURCE_EVAL_OUTPUT)

_FIELDS = frozenset({"id", "source", "draft", "goal", "rules", "softer", "firmer", "expected"})
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
    source: str
    draft: str
    goal: RewriteGoal | None
    rules: tuple[RuleContext, ...]
    softer: str
    firmer: str
    expected: ExpectedVerdicts

    @property
    def ladder_eligible(self) -> bool:
        """True when every slot label is good, so ``ladder_ok`` is measured on this pair."""
        return self.expected.softer.all_good and self.expected.firmer.all_good


def _text(raw: dict[str, object], field_name: str) -> str:
    value = raw.get(field_name)
    if not isinstance(value, str) or not value.strip():
        msg = f"{field_name} must be a non-empty string"
        raise ValueError(msg)
    return value


def _source(raw: object) -> str:
    if raw not in SOURCES:
        msg = f"source must be one of {', '.join(SOURCES)}"
        raise ValueError(msg)
    return str(raw)


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
        source=_source(fields["source"]),
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
