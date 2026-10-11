"""Port for one-time sealed compose handoffs (inline → mini-app)."""

from __future__ import annotations

from typing import Protocol

from svoi_pravila.application.compose_handoff import ComposeHandoffPayload


class ComposeHandoffs(Protocol):
    """Seal and single-use redeem of bearer compose handoff payloads."""

    async def store(self, payload: ComposeHandoffPayload) -> str:
        """Encrypt ``payload`` and return an opaque ``h_`` token."""

    async def redeem_once(self, token: str) -> ComposeHandoffPayload:
        """Atomically delete and decrypt; miss/reuse/tamper → ComposeHandoffUnavailable."""
