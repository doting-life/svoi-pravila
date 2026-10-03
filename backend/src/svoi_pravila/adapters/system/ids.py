"""UUIDv7 identifier generator."""

from __future__ import annotations

import uuid

import uuid_utils.compat as uuid_compat


class Uuid7IdGenerator:
    """Generate monotonic UUIDv7 values as stdlib ``uuid.UUID``."""

    def new_id(self) -> uuid.UUID:
        """Return the next UUIDv7 for this process."""
        return uuid_compat.uuid7()
