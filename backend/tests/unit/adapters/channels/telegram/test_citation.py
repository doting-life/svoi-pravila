"""Applied-rule citation helper."""

from __future__ import annotations

from pathlib import Path

import pytest

from svoi_pravila.adapters.channels.telegram.localization import format_applied_rule_citation

_REPO_ROOT = Path(__file__).resolve().parents[6]
_CITATION_FIXTURE = _REPO_ROOT / "testdata" / "decode_rule_citation_line.txt"


@pytest.mark.unit
def test_citation_copy_matches_task_examples() -> None:
    cited = format_applied_rule_citation(date="3 октября", text="не повышать голос")
    expected = _CITATION_FIXTURE.read_text(encoding="utf-8").strip()
    assert cited == expected
    cited_year = format_applied_rule_citation(date="15 января 2025", text="не повышать голос")
    assert cited_year == "Учтено правило от 15 января 2025: «не повышать голос»"
