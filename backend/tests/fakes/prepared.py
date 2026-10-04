"""In-memory PreparedResults for tests."""

from __future__ import annotations

from dataclasses import dataclass, field

from svoi_pravila.application.errors import PreparedResultUnavailable
from svoi_pravila.application.ports.prepared_results import PreparedVariant
from svoi_pravila.application.prepared_token import TOKEN_LENGTH, TOKEN_PREFIX, is_prepared_token


@dataclass
class FakePreparedResults:
    """Process-memory prepared variants keyed by token."""

    items: dict[str, tuple[str, PreparedVariant]] = field(default_factory=dict)
    store_calls: int = 0

    async def store(self, user_pseudonym: str, variant: PreparedVariant) -> str:
        self.store_calls += 1
        token = f"{TOKEN_PREFIX}{'A' * 64}"
        if self.items:
            token = f"{TOKEN_PREFIX}{self.store_calls:064d}"[:TOKEN_LENGTH]
        if not is_prepared_token(token):
            token = TOKEN_PREFIX + "B" * 64
        self.items[token] = (user_pseudonym, variant)
        return token

    async def redeem(self, user_pseudonym: str, token: str) -> PreparedVariant:
        if not is_prepared_token(token) or token not in self.items:
            raise PreparedResultUnavailable()
        owner, variant = self.items[token]
        if owner != user_pseudonym:
            raise PreparedResultUnavailable()
        return variant

    async def delete(self, user_pseudonym: str, token: str) -> None:
        await self.redeem(user_pseudonym, token)
        del self.items[token]
