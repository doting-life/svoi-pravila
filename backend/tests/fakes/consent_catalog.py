"""Fake consent catalog."""

from __future__ import annotations

from svoi_pravila.domain.access import AccessRequirement, ConsentText
from svoi_pravila.domain.consent_document import ConsentDocument
from svoi_pravila.domain.enums import ConsentKind
from svoi_pravila.domain.text import Sha256Hex


class FakeConsentCatalog:
    """In-memory consent catalog with a fixed requirement and document bodies."""

    def __init__(self, requirement: AccessRequirement | None = None) -> None:
        self._requirement = requirement or AccessRequirement.from_kinds(
            {
                ConsentKind.PERSONAL_DATA: ConsentText("1", Sha256Hex("a" * 64)),
                ConsentKind.SPECIAL_CATEGORY: ConsentText("1", Sha256Hex("b" * 64)),
            }
        )
        self._texts = {
            ConsentKind.PERSONAL_DATA: "personal data consent body",
            ConsentKind.SPECIAL_CATEGORY: "special category consent body",
        }

    def current_requirement(self) -> AccessRequirement:
        """Return the current requirement mapping."""
        return self._requirement

    def current_document(self, kind: ConsentKind) -> ConsentDocument:
        """Return the current document including text."""
        text_id = self._requirement.for_kind(kind)
        return ConsentDocument(
            kind=kind,
            version=text_id.version,
            sha256=text_id.sha256,
            text=self._texts[kind],
        )

    def set_requirement(self, requirement: AccessRequirement) -> None:
        """Replace the current requirement (simulates text version bump)."""
        self._requirement = requirement

    def set_text(self, kind: ConsentKind, text: str) -> None:
        """Replace the document body for ``kind``."""
        self._texts[kind] = text
