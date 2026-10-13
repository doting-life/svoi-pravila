"""Static Russian tone-suggestion rule templates (C0 catalog, no LLM)."""

from __future__ import annotations

from svoi_pravila.domain.enums import VariantKind
from svoi_pravila.domain.text import RuleText

_TEMPLATES: dict[VariantKind, RuleText] = {
    VariantKind.SOFTER: RuleText("Говорить мягко, без резких формулировок"),
    VariantKind.FIRMER: RuleText("Говорить прямо и твёрдо, без долгих смягчений"),
}


class StaticToneSuggestionCatalog:
    """In-process catalog of tone rule templates keyed by variant kind."""

    def template(self, kind: VariantKind) -> RuleText:
        """Return the catalog template for ``kind``."""
        return _TEMPLATES[kind]
