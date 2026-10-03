"""Id generator port."""

from __future__ import annotations

import uuid
from typing import Protocol


class IdGenerator(Protocol):
    """Generates new entity identifiers."""

    def new_id(self) -> uuid.UUID:
        """Return a new UUIDv7 (or test substitute)."""
        ...
