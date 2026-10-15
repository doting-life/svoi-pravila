"""Production GenerationAttemptObserver: drops every attempt, stores nothing."""

from __future__ import annotations

from svoi_pravila.application.ports.generation_attempt_observer import InvalidAttempt


class NoopGenerationAttemptObserver:
    """Ignores invalid attempts so that no conversation text outlives the call."""

    def on_invalid_attempt(self, attempt: InvalidAttempt) -> None:
        """Discard ``attempt``."""
