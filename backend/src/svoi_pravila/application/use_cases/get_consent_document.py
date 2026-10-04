"""Return the current consent document for a kind."""

from __future__ import annotations

from dataclasses import dataclass

from svoi_pravila.application.ports.consent_catalog import ConsentCatalog
from svoi_pravila.domain.consent_document import ConsentDocument
from svoi_pravila.domain.enums import ConsentKind


@dataclass(frozen=True, slots=True)
class GetConsentDocumentQuery:
    """Input for GetConsentDocument."""

    kind: ConsentKind


@dataclass(frozen=True, slots=True)
class GetConsentDocumentResult:
    """Result of GetConsentDocument."""

    document: ConsentDocument


class GetConsentDocument:
    """Expose the current consent document to channels without resource I/O."""

    def __init__(self, catalog: ConsentCatalog) -> None:
        self._catalog = catalog

    async def execute(self, query: GetConsentDocumentQuery) -> GetConsentDocumentResult:
        """Return the current document for the requested kind."""
        return GetConsentDocumentResult(document=self._catalog.current_document(query.kind))
