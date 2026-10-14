"""Calibrate the rewrite judge against approved labels (ADR-0016 §13).

The judge grades every calibration item once. Per criterion the numbers are:

- ``accuracy``: the share of labelled values (``na`` is a label) the judge reproduced exactly;
- ``defect_recall``: among the values labelled as a defect, the share the judge also called a
  defect. A defect is ``no`` for ``clean``, ``meaning_kept``, ``goal_done``, ``rule_done`` and
  ``ladder_ok``, and ``yes`` for ``invented``.

A judge call without verdicts reproduces nothing and finds no defect. The calibration is
``passed`` when every criterion has accuracy >= 0.90 and defect recall >= 0.85, and ``clean``
has defect recall >= 0.95. The committed file holds numbers only, never text.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from svoi_pravila.application.errors import (
    GenerationRefusedByProvider,
    GenerationUnavailable,
    InvalidGenerationOutput,
    UnavailableKind,
)
from svoi_pravila.application.ports.rewrite_judge import (
    JudgeRequest,
    JudgeResult,
    RewriteJudge,
    SlotVerdicts,
    Verdict,
)
from svoi_pravila.benchmarks.runner import SpendTracker
from svoi_pravila.evals.judge_cases import JudgeCase

RUBRIC_VERSION = "judge_rewrite@v1"
MIN_ACCURACY = 0.90
MIN_DEFECT_RECALL = 0.85
MIN_CLEAN_DEFECT_RECALL = 0.95

SLOT_CRITERIA = ("clean", "meaning_kept", "invented", "goal_done", "rule_done")
PAIR_CRITERION = "ladder_ok"
CRITERIA = (*SLOT_CRITERIA, PAIR_CRITERION)

_FILE_VERSION = 1
_RATE_DIGITS = 4
_MODEL_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_STOP_KINDS = frozenset({UnavailableKind.RATE_LIMITED, UnavailableKind.AUTH})
_SLOTS = ("softer", "firmer")


@dataclass(frozen=True, slots=True)
class ItemOutcome:
    """The judge's answer for one item; ``result`` is ``None`` when the call gave no verdicts."""

    case: JudgeCase
    result: JudgeResult | None


@dataclass(slots=True)
class CalibrationRun:
    """Outcomes plus the reason a run stopped early, if it did."""

    outcomes: list[ItemOutcome] = field(default_factory=list)
    stop_reason: str | None = None
    spent_billable: int = 0


@dataclass(frozen=True, slots=True)
class CriterionStats:
    """Numbers of one criterion; ``None`` where there is no denominator."""

    criterion: str
    labels: int
    accuracy: float | None
    defects: int
    defect_recall: float | None

    def passes(self) -> bool:
        """True when accuracy and defect recall reach their thresholds."""
        floor = MIN_CLEAN_DEFECT_RECALL if self.criterion == "clean" else MIN_DEFECT_RECALL
        return (
            self.accuracy is not None
            and self.defect_recall is not None
            and self.accuracy >= MIN_ACCURACY
            and self.defect_recall >= floor
        )


@dataclass(frozen=True, slots=True)
class Calibration:
    """The committed result of one calibration run."""

    model: str
    rubric: str
    items: int
    failed_items: int
    criteria: tuple[CriterionStats, ...]

    @property
    def passed(self) -> bool:
        """True when every criterion passes."""
        return all(stats.passes() for stats in self.criteria)


@dataclass(frozen=True, slots=True)
class Disagreement:
    """One value the judge got wrong, for the local report."""

    case: JudgeCase
    slot: str | None
    criterion: str
    expected: Verdict
    got: Verdict | None


def _share(part: int, whole: int) -> float | None:
    return None if whole == 0 else round(part / whole, _RATE_DIGITS)


def defect_value(criterion: str) -> Verdict:
    """The verdict that marks a defect for ``criterion``."""
    return Verdict.YES if criterion == "invented" else Verdict.NO


def _slot_value(slot: SlotVerdicts, criterion: str) -> Verdict:
    return Verdict(getattr(slot, criterion))


def _values(
    outcome: ItemOutcome, criterion: str
) -> list[tuple[str | None, Verdict, Verdict | None]]:
    """(slot, expected, got) for each value of ``criterion`` in one item."""
    expected = outcome.case.expected
    result = outcome.result
    if criterion == PAIR_CRITERION:
        return [(None, expected.ladder_ok, None if result is None else result.ladder_ok)]
    return [
        (
            slot,
            _slot_value(getattr(expected, slot), criterion),
            None if result is None else _slot_value(getattr(result, slot), criterion),
        )
        for slot in _SLOTS
    ]


def criterion_stats(outcomes: list[ItemOutcome], criterion: str) -> CriterionStats:
    """Accuracy and defect recall of one criterion over the outcomes."""
    values = [value for outcome in outcomes for value in _values(outcome, criterion)]
    defect = defect_value(criterion)
    defects = [(expected, got) for _, expected, got in values if expected is defect]
    return CriterionStats(
        criterion=criterion,
        labels=len(values),
        accuracy=_share(sum(expected is got for _, expected, got in values), len(values)),
        defects=len(defects),
        defect_recall=_share(sum(got is defect for _, got in defects), len(defects)),
    )


def calibrate(model: str, outcomes: list[ItemOutcome]) -> Calibration:
    """Reduce the outcomes of a complete run to the committed numbers."""
    return Calibration(
        model=model,
        rubric=RUBRIC_VERSION,
        items=len(outcomes),
        failed_items=sum(outcome.result is None for outcome in outcomes),
        criteria=tuple(criterion_stats(outcomes, criterion) for criterion in CRITERIA),
    )


def disagreements(outcomes: list[ItemOutcome]) -> list[Disagreement]:
    """Every value the judge did not reproduce, in item order."""
    return [
        Disagreement(outcome.case, slot, criterion, expected, got)
        for outcome in outcomes
        for criterion in CRITERIA
        for slot, expected, got in _values(outcome, criterion)
        if expected is not got
    ]


def judge_request(case: JudgeCase, deadline_seconds: float) -> JudgeRequest:
    return JudgeRequest(
        draft=case.draft,
        goal=case.goal,
        rules=case.rules,
        softer=case.softer,
        firmer=case.firmer,
        deadline_seconds=deadline_seconds,
    )


async def run_calibration(
    judge: RewriteJudge,
    cases: list[JudgeCase],
    *,
    deadline_seconds: float,
    max_tokens: int,
    spend: SpendTracker,
) -> CalibrationRun:
    """Judge every item once; stop before a call that could cross ``max_tokens``.

    Stops at the first rate-limit or auth failure; never retries. Other failures are items
    without verdicts.
    """
    run = CalibrationRun()
    for case in cases:
        request = judge_request(case, deadline_seconds)
        if spend.spent_billable + judge.max_billable(request) > max_tokens:
            run.stop_reason = "token_budget"
            break
        try:
            result = await judge.judge(request)
        except (GenerationRefusedByProvider, InvalidGenerationOutput, GenerationUnavailable) as exc:
            spend.add(exc.usage.billable)
            run.outcomes.append(ItemOutcome(case, None))
            if isinstance(exc, GenerationUnavailable) and exc.kind in _STOP_KINDS:
                run.stop_reason = exc.kind.value
                break
            continue
        spend.add(result.meta.usage.billable)
        run.outcomes.append(ItemOutcome(case, result))
    run.spent_billable = spend.spent_billable
    return run


def calibration_path(directory: Path, model: str) -> Path:
    """``judge_calibration_<model>_v1.json`` inside ``directory``."""
    if _MODEL_NAME.fullmatch(model) is None:
        msg = f"model name {model!r} cannot be used in a file name"
        raise ValueError(msg)
    return directory / f"judge_calibration_{model}_v1.json"


def calibration_payload(calibration: Calibration) -> dict[str, object]:
    """The C0 content of the committed file: rubric, model and per-criterion numbers."""
    return {
        "version": _FILE_VERSION,
        "rubric": calibration.rubric,
        "model": calibration.model,
        "items": calibration.items,
        "failed_items": calibration.failed_items,
        "criteria": {
            stats.criterion: {
                "labels": stats.labels,
                "accuracy": stats.accuracy,
                "defects": stats.defects,
                "defect_recall": stats.defect_recall,
            }
            for stats in calibration.criteria
        },
        "passed": calibration.passed,
    }


def write_calibration(directory: Path, calibration: Calibration) -> Path:
    """Write the committed file for the calibrated model; returns its path."""
    path = calibration_path(directory, calibration.model)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(calibration_payload(calibration), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def judge_is_calibrated(directory: Path, model: str) -> bool:
    """True when ``directory`` holds a passed file for ``model`` and the current rubric.

    A missing file is not calibrated; a file of any other shape is an error.
    """
    path = calibration_path(directory, model)
    if not path.exists():
        return False
    raw = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(raw, dict)
        or raw.get("version") != _FILE_VERSION
        or not isinstance(raw.get("passed"), bool)
    ):
        msg = f"{path}: not a version {_FILE_VERSION} judge calibration file"
        raise ValueError(msg)
    return raw["passed"] and raw.get("rubric") == RUBRIC_VERSION and raw.get("model") == model


def _percent(value: float | None) -> str:
    return "no data" if value is None else f"{value * 100:.1f} %"


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def render_calibration_report(
    calibration: Calibration, outcomes: list[ItemOutcome], *, spent_billable: int
) -> str:
    """The local Markdown report: the numbers, then every disagreement with the texts."""
    lines = [
        "# Judge calibration",
        "",
        f"- model: {calibration.model}",
        f"- rubric: {calibration.rubric}",
        f"- items: {calibration.items} ({calibration.failed_items} without verdicts)",
        f"- billable tokens: {spent_billable}",
        f"- passed: {'yes' if calibration.passed else 'no'}",
        "",
        "| criterion | labels | accuracy | defects | defect recall | status |",
        "|---|---:|---:|---:|---:|---|",
        *(
            f"| {stats.criterion} | {stats.labels} | {_percent(stats.accuracy)} | {stats.defects} "
            f"| {_percent(stats.defect_recall)} | {'PASS' if stats.passes() else 'FAIL'} |"
            for stats in calibration.criteria
        ),
        "",
        "## Disagreements",
        "",
        "| item | slot | criterion | expected | judge | draft | softer | firmer |",
        "|---|---|---|---|---|---|---|---|",
    ]
    lines.extend(
        f"| {item.case.id} | {item.slot or 'pair'} | {item.criterion} | {item.expected.value} "
        f"| {'none' if item.got is None else item.got.value} | {_cell(item.case.draft)} "
        f"| {_cell(item.case.softer)} | {_cell(item.case.firmer)} |"
        for item in disagreements(outcomes)
    )
    lines.append("")
    return "\n".join(lines)
