"""Static Russian tone-suggestion rule templates (C0 catalog, no LLM)."""

from __future__ import annotations

from svoi_pravila.domain.enums import Firmness
from svoi_pravila.domain.text import RuleText

_TEMPLATES: dict[Firmness, RuleText] = {
    Firmness.GENTLE: RuleText("Говорить мягко, без резких формулировок"),
    Firmness.BALANCED: RuleText("Говорить спокойно и по делу"),
    Firmness.FIRM: RuleText("Говорить прямо и твёрдо, без долгих смягчений"),
}


class StaticToneSuggestionCatalog:
    """In-process catalog of tone rule templates keyed by firmness."""

    def template(self, firmness: Firmness) -> RuleText:
        """Return the catalog template for ``firmness``."""
        return _TEMPLATES[firmness]
