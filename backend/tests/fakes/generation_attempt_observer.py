"""In-memory GenerationAttemptObserver recording every invalid attempt."""

from __future__ import annotations

from svoi_pravila.application.ports.generation_attempt_observer import InvalidAttempt


class RecordingAttemptObserver:
    """Collects the invalid attempts a generator reports."""

    def __init__(self) -> None:
        self.attempts: list[InvalidAttempt] = []

    def on_invalid_attempt(self, attempt: InvalidAttempt) -> None:
        self.attempts.append(attempt)
