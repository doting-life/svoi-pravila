"""Calibrate the clean judge against approved labels (ADR-0016 §13).

The judge checks every calibration item once; each item has two labelled values, one per variant.
The numbers are:

- ``accuracy``: the share of labelled values the judge reproduced exactly;
- ``defect_recall``: among the values labelled ``clean = no``, the share the judge also called
  ``no``.

A judge call without verdicts reproduces nothing and finds no defect. The numbers are given
overall and per source of the items (``synthetic``, ``eval_output``). The calibration is
``passed`` when, overall, accuracy >= 0.90 and defect recall >= 0.95, and on ``eval_output``
accuracy >= 0.85 and defect recall >= 0.95. The committed file holds numbers only, never text.
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
    JudgeObservation,
    JudgeRequest,
    JudgeResult,
    RewriteJudge,
    Verdict,
)
from svoi_pravila.benchmarks.runner import SpendTracker
from svoi_pravila.evals.judge_cases import SOURCE_EVAL_OUTPUT, SOURCE_SYNTHETIC, SOURCES, JudgeCase
from svoi_pravila.evals.judge_verdicts import derive_result, evidence, ungrounded_counts

RUBRIC_VERSION = "judge_clean@v1"
MIN_ACCURACY = 0.90
MIN_DEFECT_RECALL = 0.95
MIN_REAL_ACCURACY = 0.85
MIN_REAL_DEFECT_RECALL = 0.95

_FILE_VERSION = 4
_RATE_DIGITS = 4
_MODEL_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_STOP_KINDS = frozenset({UnavailableKind.RATE_LIMITED, UnavailableKind.AUTH})
_SLOTS = ("softer", "firmer")


@dataclass(frozen=True, slots=True)
class ItemOutcome:
    """The judge's answer for one item; ``result`` is ``None`` when the call gave no verdicts.

    ``observation`` holds the judge's quotes for the local report; it is never committed.
    """

    case: JudgeCase
    result: JudgeResult | None
    observation: JudgeObservation | None = None


@dataclass(slots=True)
class CalibrationRun:
    """Outcomes plus the reason a run stopped early, if it did."""

    outcomes: list[ItemOutcome] = field(default_factory=list)
    stop_reason: str | None = None
    spent_billable: int = 0


@dataclass(frozen=True, slots=True)
class CleanStats:
    """Numbers of ``clean`` over a set of items; ``None`` where there is no denominator."""

    labels: int
    accuracy: float | None
    defects: int
    defect_recall: float | None

    def passes(self, *, min_accuracy: float, min_recall: float) -> bool:
        """True when accuracy and defect recall reach the given thresholds."""
        return (
            self.accuracy is not None
            and self.defect_recall is not None
            and self.accuracy >= min_accuracy
            and self.defect_recall >= min_recall
        )


@dataclass(frozen=True, slots=True)
class Calibration:
    """The committed result of one calibration run."""

    model: str
    rubric: str
    items: int
    failed_items: int
    overall: CleanStats
    by_source: tuple[tuple[str, CleanStats], ...]
    ungrounded_softer: int
    ungrounded_firmer: int

    @property
    def ungrounded(self) -> int:
        """Quotes that are not in their own variant, over all items."""
        return self.ungrounded_softer + self.ungrounded_firmer

    @property
    def passed(self) -> bool:
        """True when ``clean`` passes overall and on the ``eval_output`` items."""
        real = dict(self.by_source)[SOURCE_EVAL_OUTPUT]
        return self.overall.passes(
            min_accuracy=MIN_ACCURACY, min_recall=MIN_DEFECT_RECALL
        ) and real.passes(min_accuracy=MIN_REAL_ACCURACY, min_recall=MIN_REAL_DEFECT_RECALL)


@dataclass(frozen=True, slots=True)
class Disagreement:
    """One value the judge got wrong, for the local report."""

    case: JudgeCase
    slot: str
    expected: Verdict
    got: Verdict | None
    evidence: str


def _share(part: int, whole: int) -> float | None:
    return None if whole == 0 else round(part / whole, _RATE_DIGITS)


def _values(outcome: ItemOutcome) -> list[tuple[str, Verdict, Verdict | None]]:
    """(slot, expected, got) for the two values of one item."""
    expected = outcome.case.expected
    result = outcome.result
    return [
        (
            slot,
            getattr(expected, slot),
            None if result is None else getattr(result, f"{slot}_clean"),
        )
        for slot in _SLOTS
    ]


def clean_stats(outcomes: list[ItemOutcome]) -> CleanStats:
    """Accuracy and defect recall of ``clean`` over the outcomes."""
    values = [value for outcome in outcomes for value in _values(outcome)]
    defects = [got for _, expected, got in values if expected is Verdict.NO]
    return CleanStats(
        labels=len(values),
        accuracy=_share(sum(expected is got for _, expected, got in values), len(values)),
        defects=len(defects),
        defect_recall=_share(sum(got is Verdict.NO for got in defects), len(defects)),
    )


def calibrate(model: str, outcomes: list[ItemOutcome]) -> Calibration:
    """Reduce the outcomes of a complete run to the committed numbers."""
    counts = [
        ungrounded_counts(judge_request(outcome.case, 1.0), outcome.observation)
        for outcome in outcomes
        if outcome.observation is not None
    ]
    return Calibration(
        model=model,
        rubric=RUBRIC_VERSION,
        items=len(outcomes),
        failed_items=sum(outcome.result is None for outcome in outcomes),
        overall=clean_stats(outcomes),
        by_source=tuple(
            (source, clean_stats([item for item in outcomes if item.case.source == source]))
            for source in SOURCES
        ),
        ungrounded_softer=sum(count.softer for count in counts),
        ungrounded_firmer=sum(count.firmer for count in counts),
    )


def disagreements(outcomes: list[ItemOutcome]) -> list[Disagreement]:
    """Every value the judge did not reproduce, in item order."""
    return [
        Disagreement(
            outcome.case,
            slot,
            expected,
            got,
            "-"
            if outcome.observation is None
            else evidence(judge_request(outcome.case, 1.0), outcome.observation, slot),
        )
        for outcome in outcomes
        for slot, expected, got in _values(outcome)
        if expected is not got
    ]


def judge_request(case: JudgeCase, deadline_seconds: float) -> JudgeRequest:
    return JudgeRequest(softer=case.softer, firmer=case.firmer, deadline_seconds=deadline_seconds)


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
            observation = await judge.judge(request)
        except (GenerationRefusedByProvider, InvalidGenerationOutput, GenerationUnavailable) as exc:
            spend.add(exc.usage.billable)
            run.outcomes.append(ItemOutcome(case, None))
            if isinstance(exc, GenerationUnavailable) and exc.kind in _STOP_KINDS:
                run.stop_reason = exc.kind.value
                break
            continue
        spend.add(observation.meta.usage.billable)
        run.outcomes.append(ItemOutcome(case, derive_result(request, observation), observation))
    run.spent_billable = spend.spent_billable
    return run


def _file_stem(model: str) -> str:
    if _MODEL_NAME.fullmatch(model) is None:
        msg = f"model name {model!r} cannot be used in a file name"
        raise ValueError(msg)
    return f"judge_calibration_{model}_{RUBRIC_VERSION.rsplit('@', 1)[1]}"


def report_name(model: str) -> str:
    """The default local report file name: it carries the model and the rubric version."""
    return f"{_file_stem(model)}.md"


def calibration_path(directory: Path, model: str) -> Path:
    """``judge_calibration_<model>_<rubric version>.json`` inside ``directory``."""
    return directory / f"{_file_stem(model)}.json"


def _stats_payload(stats: CleanStats) -> dict[str, object]:
    return {
        "labels": stats.labels,
        "accuracy": stats.accuracy,
        "defects": stats.defects,
        "defect_recall": stats.defect_recall,
    }


def calibration_payload(calibration: Calibration) -> dict[str, object]:
    """The C0 content of the committed file: rubric, model and the numbers of ``clean``."""
    return {
        "version": _FILE_VERSION,
        "rubric": calibration.rubric,
        "model": calibration.model,
        "items": calibration.items,
        "failed_items": calibration.failed_items,
        "clean": _stats_payload(calibration.overall),
        "sources": {source: _stats_payload(stats) for source, stats in calibration.by_source},
        "ungrounded": {
            "softer": calibration.ungrounded_softer,
            "firmer": calibration.ungrounded_firmer,
            "total": calibration.ungrounded,
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


def _stats_row(title: str, stats: CleanStats, status: str) -> str:
    return (
        f"| {title} | {stats.labels} | {_percent(stats.accuracy)} | {stats.defects} "
        f"| {_percent(stats.defect_recall)} | {status} |"
    )


def render_calibration_report(
    calibration: Calibration, outcomes: list[ItemOutcome], *, spent_billable: int
) -> str:
    """The local Markdown report: the numbers, then every disagreement with the texts."""
    by_source = dict(calibration.by_source)
    overall_ok = calibration.overall.passes(min_accuracy=MIN_ACCURACY, min_recall=MIN_DEFECT_RECALL)
    real_ok = by_source[SOURCE_EVAL_OUTPUT].passes(
        min_accuracy=MIN_REAL_ACCURACY, min_recall=MIN_REAL_DEFECT_RECALL
    )
    lines = [
        "# Judge calibration (clean)",
        "",
        f"- model: {calibration.model}",
        f"- rubric: {calibration.rubric}",
        f"- items: {calibration.items} ({calibration.failed_items} without verdicts)",
        f"- billable tokens: {spent_billable}",
        f"- ungrounded quotes: {calibration.ungrounded} "
        f"(softer {calibration.ungrounded_softer}, firmer {calibration.ungrounded_firmer})",
        f"- passed: {'yes' if calibration.passed else 'no'}",
        "",
        "| set | labels | accuracy | defects | defect recall | status |",
        "|---|---:|---:|---:|---:|---|",
        _stats_row("overall", calibration.overall, "PASS" if overall_ok else "FAIL"),
        _stats_row(SOURCE_SYNTHETIC, by_source[SOURCE_SYNTHETIC], "-"),
        _stats_row(
            SOURCE_EVAL_OUTPUT, by_source[SOURCE_EVAL_OUTPUT], "PASS" if real_ok else "FAIL"
        ),
        "",
        "## Disagreements",
        "",
        "| item | slot | expected | judge | evidence | softer | firmer |",
        "|---|---|---|---|---|---|---|",
    ]
    lines.extend(
        f"| {item.case.id} | {item.slot} | {item.expected.value} "
        f"| {'none' if item.got is None else item.got.value} | {_cell(item.evidence)} "
        f"| {_cell(item.case.softer)} | {_cell(item.case.firmer)} |"
        for item in disagreements(outcomes)
    )
    lines.append("")
    return "\n".join(lines)
