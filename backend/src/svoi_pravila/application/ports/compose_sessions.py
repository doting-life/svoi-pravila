"""Port for reusable sealed compose sessions."""

from __future__ import annotations

from typing import Protocol

from svoi_pravila.application.compose_session import ComposeSessionPayload


class ComposeSessions(Protocol):
    """Seal and load bearer compose session payloads (GET, not GETDEL)."""

    async def store(self, payload: ComposeSessionPayload) -> str:
        """Encrypt ``payload`` and return an opaque ``cs_`` token."""

    async def load(self, token: str) -> ComposeSessionPayload:
        """Decrypt without deleting; miss/tamper → ComposeSessionUnavailable."""
