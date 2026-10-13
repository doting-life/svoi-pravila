"""Port for single-use compose choice tokens (no draft/variant text)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from svoi_pravila.domain.enums import UsageScenario
from svoi_pravila.domain.ids import ContactId

COMPOSE_CHOICE_TTL_SECONDS = 600
COMPOSE_CHOICE_FIRMNESS_SET = "gentle,balanced,firm"


@dataclass(frozen=True, slots=True)
class ComposeChoiceBinding:
    """What a token binds at issue time: the scenario and the contact used (C0/C1)."""

    scenario: UsageScenario
    contact_id: ContactId | None


class ComposeChoiceTokens(Protocol):
    """Issue and redeem HMAC-bound compose choice tokens."""

    async def issue(
        self,
        user_pseudonym: str,
        scenario: UsageScenario,
        contact_id: ContactId | None,
    ) -> str:
        """Store a single-use token bound to scenario and contact; return its opaque string."""

    async def redeem_once(self, user_pseudonym: str, token: str) -> ComposeChoiceBinding:
        """Verify, consume, and return the bound scenario and contact (or raise)."""
