"""Port for diagnosing invalid generation attempts (evals only; production stores nothing).

An observer receives the text of an invalid attempt, which is conversation text. Production
binds a no-op implementation; only the local eval tool binds a recorder, and its rows go into
the local report, never into logs, metrics or the repository.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from svoi_pravila.application.errors import InvalidOutputReason
from svoi_pravila.domain.enums import VariantKind


@dataclass(frozen=True, slots=True)
class InvalidAttempt:
    """One attempt whose output failed validation.

    ``texts`` are ``(field, text)`` pairs of the parsed answer, or one ``("raw", text)`` pair when
    the answer did not parse; they are conversation text and stay out of ``repr``.
    """

    operation: str
    attempt: int
    reason: InvalidOutputReason
    slot: VariantKind | None
    texts: tuple[tuple[str, str], ...] = field(repr=False)


class GenerationAttemptObserver(Protocol):
    """Receives every invalid attempt of a structured generation call."""

    def on_invalid_attempt(self, attempt: InvalidAttempt) -> None:
        """Handle one invalid attempt; must not raise and must not log its texts."""
