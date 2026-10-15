"""Eval-only recorder of invalid generation attempts: the texts go into the local report.

The recorder is bound by the eval CLI only; ``bootstrap`` and ``api`` must never import it
(import contract). Its rows hold conversation text and are never logged or committed.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from svoi_pravila.application.errors import InvalidOutputReason
from svoi_pravila.application.ports.generation_attempt_observer import InvalidAttempt
from svoi_pravila.domain.enums import VariantKind


@dataclass(frozen=True, slots=True)
class RecordedAttempt:
    """One invalid attempt of one eval call; ``texts`` are text for the local report only."""

    case_id: str
    run_index: int
    attempt: int
    reason: InvalidOutputReason
    slot: VariantKind | None
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
                reason=attempt.reason,
                slot=attempt.slot,
                texts=attempt.texts,
            )
        )

    def take(self) -> tuple[RecordedAttempt, ...]:
        """Return the recorded attempts and forget them."""
        rows = tuple(self._rows)
        self._rows.clear()
        return rows
