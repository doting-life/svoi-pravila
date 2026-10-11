"""In-memory ComposeHandoffs for tests."""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field

from svoi_pravila.application.compose_handoff import (
    COMPOSE_HANDOFF_REF_PREFIX,
    ComposeHandoffPayload,
)
from svoi_pravila.application.errors import ComposeHandoffUnavailable


@dataclass
class FakeComposeHandoffs:
    """Process-memory one-shot bearer compose handoffs."""

    items: dict[str, ComposeHandoffPayload] = field(default_factory=dict)
    redeem_calls: int = 0
    store_calls: int = 0

    async def store(self, payload: ComposeHandoffPayload) -> str:
        self.store_calls += 1
        body = secrets.token_urlsafe(48)[:64]
        token = f"{COMPOSE_HANDOFF_REF_PREFIX}{body}"
        self.items[token] = payload
        return token

    async def redeem_once(self, token: str) -> ComposeHandoffPayload:
        self.redeem_calls += 1
        if token not in self.items:
            raise ComposeHandoffUnavailable()
        return self.items.pop(token)
