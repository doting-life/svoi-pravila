"""In-memory WelcomeThrottle fake."""

from __future__ import annotations


class FakeWelcomeThrottle:
    """Claim each pseudonym once until cleared."""

    def __init__(self) -> None:
        self._claimed: set[str] = set()

    async def claim(self, pseudonym: str) -> bool:
        if pseudonym in self._claimed:
            return False
        self._claimed.add(pseudonym)
        return True

    def clear(self) -> None:
        self._claimed.clear()
