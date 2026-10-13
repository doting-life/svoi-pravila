"""Port for tone-source rule suggestion templates (no channel strings)."""

from __future__ import annotations

from typing import Protocol

from svoi_pravila.domain.enums import VariantKind
from svoi_pravila.domain.text import RuleText


class ToneSuggestionCatalog(Protocol):
    """Returns catalog rule text for a dominant variant kind."""

    def template(self, kind: VariantKind) -> RuleText:
        """Return the tone suggestion template for ``kind`` (softer or firmer)."""
        ...
