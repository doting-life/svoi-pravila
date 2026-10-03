"""Per-unit-of-work registry of loaded/added ORM rows (strong references)."""

from __future__ import annotations

from typing import TypeVar, cast
from uuid import UUID

from svoi_pravila.adapters.persistence.errors import RowNotLoadedError

T = TypeVar("T")


class RowRegistry:
    """Strong references to rows loaded or added in the active unit of work."""

    def __init__(self) -> None:
        self._rows: dict[tuple[type[object], UUID], object] = {}
        self._contact_labels: dict[UUID, str] = {}

    def register(self, row_type: type[T], identity: UUID, row: T) -> None:
        """Retain ``row`` for the lifetime of this unit of work."""
        self._rows[(row_type, identity)] = row

    def register_contact_label(self, contact_id: UUID, label: str) -> None:
        """Snapshot plaintext label at load/add time for change detection."""
        self._contact_labels[contact_id] = label

    def contact_label(self, contact_id: UUID) -> str | None:
        """Return the label snapshot if this contact was registered."""
        return self._contact_labels.get(contact_id)

    def get(self, row_type: type[T], identity: UUID) -> T | None:
        """Return a registered row or ``None``."""
        row = self._rows.get((row_type, identity))
        if row is None:
            return None
        return cast(T, row)

    def require(self, row_type: type[T], identity: UUID) -> T:
        """Return a registered row or raise ``RowNotLoadedError``."""
        row = self.get(row_type, identity)
        if row is None:
            raise RowNotLoadedError(row_type, identity)
        return row
