"""Port for one-time sealed compose handoffs (inline → mini-app)."""

from __future__ import annotations

from typing import Protocol

from svoi_pravila.application.compose_handoff import ComposeHandoffPayload


class ComposeHandoffs(Protocol):
    """Seal and single-use redeem of compose handoff payloads."""

    async def store(self, user_pseudonym: str, payload: ComposeHandoffPayload) -> str:
        """Encrypt ``payload`` and return an opaque ``h_`` token."""

    async def redeem_once(self, user_pseudonym: str, token: str) -> ComposeHandoffPayload:
        """Atomically delete and decrypt; miss/reuse/wrong user → ComposeHandoffUnavailable."""
