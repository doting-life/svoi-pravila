"""Port for tone-source rule suggestion templates (no channel strings)."""

from __future__ import annotations

from typing import Protocol

from svoi_pravila.domain.enums import Firmness
from svoi_pravila.domain.text import RuleText


class ToneSuggestionCatalog(Protocol):
    """Returns catalog rule text for a dominant firmness."""

    def template(self, firmness: Firmness) -> RuleText:
        """Return the tone suggestion template for ``firmness``."""
        ...
