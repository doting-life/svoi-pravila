"""In-memory ComposeChoiceTokens for unit tests."""

from __future__ import annotations

import secrets

from svoi_pravila.application.errors import ComposeChoiceTokenInvalid
from svoi_pravila.application.ports.compose_choice_tokens import ComposeChoiceBinding
from svoi_pravila.domain.enums import UsageScenario
from svoi_pravila.domain.ids import ContactId


class FakeComposeChoiceTokens:
    """Issue/redeem map keyed by opaque token string."""

    def __init__(self) -> None:
        self._by_token: dict[str, tuple[str, ComposeChoiceBinding]] = {}

    async def issue(
        self,
        user_pseudonym: str,
        scenario: UsageScenario,
        contact_id: ContactId | None,
    ) -> str:
        token = f"c_fake_{secrets.token_hex(8)}"
        self._by_token[token] = (user_pseudonym, ComposeChoiceBinding(scenario, contact_id))
        return token

    async def redeem_once(self, user_pseudonym: str, token: str) -> ComposeChoiceBinding:
        row = self._by_token.pop(token, None)
        if row is None:
            raise ComposeChoiceTokenInvalid()
        owner, binding = row
        if owner != user_pseudonym:
            raise ComposeChoiceTokenInvalid()
        return binding
