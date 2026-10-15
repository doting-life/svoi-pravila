"""Eval-only recorder of invalid generation attempts: the texts go into the local report.

The recorder is bound by the eval CLI only; ``bootstrap`` and ``api`` must never import it
(import contract). Its rows hold conversation text and are never logged or committed.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from svoi_pravila.adapters.llm.gigachat.structured import LabelledInvalidAttempt
from svoi_pravila.application.ports.generation_attempt_observer import InvalidAttempt


@dataclass(frozen=True, slots=True)
class RecordedAttempt:
    """One invalid attempt of one eval call; ``texts`` are text for the local report only.

    ``labels`` are the ``reason[:slot]`` labels of every defect of the attempt.
    """

    case_id: str
    run_index: int
    attempt: int
    labels: tuple[str, ...]
    texts: tuple[tuple[str, str], ...] = field(repr=False)


class AttemptRecorder:
    """GenerationAttemptObserver that keeps the attempts of the current eval call in memory."""

    def __init__(self) -> None:
        self._case_id = ""
        self._run_index = 0
        self._rows: list[RecordedAttempt] = []

    def begin_call(self, case_id: str, run_index: int) -> None:
        """Name the call whose attempts are recorded next."""
        self._case_id = case_id
        self._run_index = run_index

    def on_invalid_attempt(self, attempt: InvalidAttempt) -> None:
        """Keep ``attempt`` under the current call."""
        self._rows.append(
            RecordedAttempt(
                case_id=self._case_id,
                run_index=self._run_index,
                attempt=attempt.attempt + 1,
                labels=_labels(attempt),
                texts=attempt.texts,
            )
        )

    def take(self) -> tuple[RecordedAttempt, ...]:
        """Return the recorded attempts and forget them."""
        rows = tuple(self._rows)
        self._rows.clear()
        return rows


def _labels(attempt: InvalidAttempt) -> tuple[str, ...]:
    """Every defect label of ``attempt``; the first defect when the generator names only one."""
    if isinstance(attempt, LabelledInvalidAttempt):
        return attempt.labels
    slot = attempt.slot
    return (attempt.reason.value if slot is None else f"{attempt.reason.value}:{slot.value}",)
