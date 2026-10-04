"""Consent catalog port."""

from __future__ import annotations

from typing import Protocol

from svoi_pravila.domain.access import AccessRequirement
from svoi_pravila.domain.consent_document import ConsentDocument
from svoi_pravila.domain.enums import ConsentKind


class ConsentCatalog(Protocol):
    """Provides the current consent text versions, hashes, and bodies."""

    def current_requirement(self) -> AccessRequirement:
        """Return the access requirement for all consent kinds."""
        ...

    def current_document(self, kind: ConsentKind) -> ConsentDocument:
        """Return the current document for ``kind`` including full text."""
        ...
