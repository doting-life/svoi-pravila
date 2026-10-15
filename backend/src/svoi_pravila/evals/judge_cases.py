"""Calibration items of the clean judge: a committed synthetic JSONL file (ADR-0016 §13).

Labelling contract. Every label of ``evals/data/judge_calibration_v1.jsonl`` follows this rule,
which restates the judge prompt; a label that breaks it is a defect of the set:

* R3 ``clean`` is ``no`` only for vulgar or obscene words in any form, insults and accusatory
  generalizations. Threats, taunts and coldness without such words are ``clean`` = ``yes``.

Every item has a ``source``: ``synthetic`` (written for the set) or ``eval_output`` (a real
``softer``/``firmer`` pair of a rewrite eval run, copied exactly and labelled by R3; these may
repeat drafts of ``rewrite_v2.jsonl``).

Quotas are enforced by ``tests/unit/evals/test_judge_calibration_dataset.py``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from svoi_pravila.application.ports.rewrite_judge import Verdict

SOURCE_SYNTHETIC = "synthetic"
SOURCE_EVAL_OUTPUT = "eval_output"
SOURCES = (SOURCE_SYNTHETIC, SOURCE_EVAL_OUTPUT)

_FIELDS = frozenset({"id", "source", "softer", "firmer", "expected"})
_SLOTS = frozenset({"softer", "firmer"})
_SLOT_FIELDS = frozenset({"clean"})


@dataclass(frozen=True, slots=True)
class ExpectedClean:
    """The approved ``clean`` labels of one item: one per variant."""

    softer: Verdict
    firmer: Verdict


@dataclass(frozen=True, slots=True)
class JudgeCase:
    """Two variants and the ``clean`` labels the judge is calibrated against."""

    id: str
    source: str
    softer: str
    firmer: str
    expected: ExpectedClean


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
        msg = f"{field_name} must be one of yes, no"
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


def _clean(raw: object, name: str) -> Verdict:
    fields = _object(raw, f"expected.{name}", _SLOT_FIELDS)
    return _verdict(fields["clean"], f"{name}.clean")


def parse_judge_case(raw: object) -> JudgeCase:
    """Validate one JSONL object into a JudgeCase; any other shape is an error."""
    fields = _object(raw, "item", _FIELDS)
    expected = _object(fields["expected"], "expected", _SLOTS)
    return JudgeCase(
        id=_text(fields, "id"),
        source=_source(fields["source"]),
        softer=_text(fields, "softer"),
        firmer=_text(fields, "firmer"),
        expected=ExpectedClean(
            softer=_clean(expected["softer"], "softer"),
            firmer=_clean(expected["firmer"], "firmer"),
        ),
    )


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
