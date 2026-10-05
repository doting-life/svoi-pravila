"""Port for one-time sealed decode sources behind «Сделать правилом»."""

from __future__ import annotations

from typing import Protocol

from svoi_pravila.application.rule_source import RuleSourcePayload


class RuleSources(Protocol):
    """Seal and single-use redeem of decode rule-source payloads."""

    async def store(self, user_pseudonym: str, payload: RuleSourcePayload) -> str:
        """Encrypt ``payload`` and return a token for ``sn:{token}`` callback data."""

    async def redeem_once(self, user_pseudonym: str, token: str) -> RuleSourcePayload:
        """Atomically delete and decrypt; miss/reuse/wrong user → RuleSourceUnavailable."""
