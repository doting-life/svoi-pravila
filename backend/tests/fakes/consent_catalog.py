"""Fake consent catalog."""

from __future__ import annotations

from svoi_pravila.domain.access import AccessRequirement, ConsentText
from svoi_pravila.domain.enums import ConsentKind
from svoi_pravila.domain.text import Sha256Hex


class FakeConsentCatalog:
    """In-memory consent catalog with a fixed requirement."""

    def __init__(self, requirement: AccessRequirement | None = None) -> None:
        self._requirement = requirement or AccessRequirement.from_kinds(
            {
                ConsentKind.PERSONAL_DATA: ConsentText("pd-v1", Sha256Hex("a" * 64)),
                ConsentKind.SPECIAL_CATEGORY: ConsentText("sc-v1", Sha256Hex("b" * 64)),
            }
        )

    def current_requirement(self) -> AccessRequirement:
        """Return the current requirement mapping."""
        return self._requirement

    def set_requirement(self, requirement: AccessRequirement) -> None:
        """Replace the current requirement (simulates text version bump)."""
        self._requirement = requirement
