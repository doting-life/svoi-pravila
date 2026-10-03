"""Consent catalog port."""

from __future__ import annotations

from typing import Protocol

from svoi_pravila.domain.access import AccessRequirement


class ConsentCatalog(Protocol):
    """Provides the current consent text versions and hashes."""

    def current_requirement(self) -> AccessRequirement:
        """Return the access requirement for all consent kinds."""
        ...
