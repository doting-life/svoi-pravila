"""Display-date formatting in the configured IANA zone."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from svoi_pravila.adapters.channels.telegram.dates import format_display_date
from svoi_pravila.adapters.channels.telegram.presenters import render_applied_rule_citations
from svoi_pravila.application.ports.generation import AppliedRuleView
from svoi_pravila.domain.enums import RuleCategory

_REPO_ROOT = Path(__file__).resolve().parents[6]
_CITATION_FIXTURE = _REPO_ROOT / "testdata" / "decode_rule_citation_line.txt"


@pytest.mark.unit
def test_format_display_date_omits_year_in_same_calendar_year() -> None:
    tz = ZoneInfo("Europe/Moscow")
    now = datetime(2026, 10, 4, 12, tzinfo=UTC)
    same_year = datetime(2026, 10, 3, 12, tzinfo=UTC)
    prior_year = datetime(2025, 1, 15, 12, tzinfo=UTC)
    assert format_display_date(same_year, now, tz) == "3 октября"
    assert format_display_date(prior_year, now, tz) == "15 января 2025"


@pytest.mark.unit
def test_citation_copy_matches_task_examples() -> None:
    tz = ZoneInfo("Europe/Moscow")
    now = datetime(2026, 10, 4, 12, tzinfo=UTC)
    view = AppliedRuleView(
        category=RuleCategory.OTHER,
        text="не повышать голос",
        effective_since=datetime(2026, 10, 3, 12, tzinfo=UTC),
    )
    cited = render_applied_rule_citations((view,), now=now, tz=tz)
    expected = _CITATION_FIXTURE.read_text(encoding="utf-8").strip()
    assert cited == (expected,)
    older = AppliedRuleView(
        category=RuleCategory.OTHER,
        text="не повышать голос",
        effective_since=datetime(2025, 1, 15, 12, tzinfo=UTC),
    )
    cited_year = render_applied_rule_citations((older,), now=now, tz=tz)
    assert cited_year == ("Учтено правило от 15 января 2025: «не повышать голос»",)
    many = tuple(
        AppliedRuleView(
            category=RuleCategory.OTHER,
            text=f"r{i}",
            effective_since=datetime(2026, 10, 3, 12, tzinfo=UTC),
        )
        for i in range(4)
    )
    assert len(render_applied_rule_citations(many, now=now, tz=tz)) == 3
