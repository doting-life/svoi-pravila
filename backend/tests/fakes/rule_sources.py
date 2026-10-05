"""In-memory RuleSources for tests."""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field

from svoi_pravila.application.errors import RuleSourceUnavailable
from svoi_pravila.application.rule_source import RuleSourcePayload


@dataclass
class FakeRuleSources:
    """Process-memory one-shot rule sources."""

    items: dict[str, tuple[str, RuleSourcePayload]] = field(default_factory=dict)
    redeem_calls: int = 0
    store_calls: int = 0

    async def store(self, user_pseudonym: str, payload: RuleSourcePayload) -> str:
        self.store_calls += 1
        token = secrets.token_urlsafe(40)[:54]
        self.items[token] = (user_pseudonym, payload)
        return token

    async def redeem_once(self, user_pseudonym: str, token: str) -> RuleSourcePayload:
        self.redeem_calls += 1
        if token not in self.items:
            raise RuleSourceUnavailable()
        owner, payload = self.items.pop(token)
        if owner != user_pseudonym:
            raise RuleSourceUnavailable()
        return payload
