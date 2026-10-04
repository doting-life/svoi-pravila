"""Port for one-shot encrypted prepared inline results."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from svoi_pravila.domain.enums import Firmness


@dataclass(frozen=True, slots=True)
class PreparedVariant:
    """One variant stored for later inline insertion (plaintext only in memory)."""

    firmness: Firmness
    text: str


class PreparedResults(Protocol):
    """Store, redeem, and delete prepared variant tokens."""

    async def store(self, user_pseudonym: str, variant: PreparedVariant) -> str:
        """Encrypt ``variant`` and return the ``p_`` token string."""

    async def redeem(self, user_pseudonym: str, token: str) -> PreparedVariant:
        """Decrypt for the same user; do not delete the ciphertext."""

    async def delete(self, user_pseudonym: str, token: str) -> None:
        """Delete after a successful decrypt for ``user_pseudonym``."""
