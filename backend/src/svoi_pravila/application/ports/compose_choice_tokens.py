"""Port for single-use compose choice tokens (C0; no draft/variant text)."""

from __future__ import annotations

from typing import Protocol

from svoi_pravila.domain.enums import UsageScenario

COMPOSE_CHOICE_TTL_SECONDS = 600
COMPOSE_CHOICE_FIRMNESS_SET = "gentle,balanced,firm"


class ComposeChoiceTokens(Protocol):
    """Issue and redeem HMAC-bound compose choice tokens."""

    async def issue(self, user_pseudonym: str, scenario: UsageScenario) -> str:
        """Store a single-use token for ``user_pseudonym`` and return its opaque string."""

    async def redeem_once(self, user_pseudonym: str, token: str) -> UsageScenario:
        """Verify, consume, and return the bound scenario (or raise)."""
