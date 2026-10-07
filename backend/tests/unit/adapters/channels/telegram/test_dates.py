"""Display-date formatting and applied-rule citation helper."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from svoi_pravila.adapters.channels.telegram.dates import format_display_date
from svoi_pravila.adapters.channels.telegram.localization import format_applied_rule_citation

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
    date = format_display_date(datetime(2026, 10, 3, 12, tzinfo=UTC), now, tz)
    cited = format_applied_rule_citation(date=date, text="не повышать голос")
    expected = _CITATION_FIXTURE.read_text(encoding="utf-8").strip()
    assert cited == expected
    date_year = format_display_date(datetime(2025, 1, 15, 12, tzinfo=UTC), now, tz)
    cited_year = format_applied_rule_citation(date=date_year, text="не повышать голос")
    assert cited_year == "Учтено правило от 15 января 2025: «не повышать голос»"
