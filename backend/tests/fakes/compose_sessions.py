"""In-memory ComposeSessions for tests."""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field

from svoi_pravila.application.compose_session import (
    COMPOSE_SESSION_REF_PREFIX,
    ComposeSessionPayload,
)
from svoi_pravila.application.errors import ComposeSessionUnavailable


@dataclass
class FakeComposeSessions:
    """Process-memory reusable bearer compose sessions."""

    items: dict[str, ComposeSessionPayload] = field(default_factory=dict)
    store_calls: int = 0
    load_calls: int = 0

    async def store(self, payload: ComposeSessionPayload) -> str:
        self.store_calls += 1
        body = secrets.token_urlsafe(48)[:64]
        token = f"{COMPOSE_SESSION_REF_PREFIX}{body}"
        self.items[token] = payload
        return token

    async def load(self, token: str) -> ComposeSessionPayload:
        self.load_calls += 1
        if token not in self.items:
            raise ComposeSessionUnavailable()
        return self.items[token]
