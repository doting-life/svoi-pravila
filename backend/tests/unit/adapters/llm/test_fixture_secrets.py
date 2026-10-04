"""Ensure committed GigaChat HTTP fixtures never contain credentials."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_FIXTURES = Path(__file__).resolve().parents[3] / "fixtures" / "gigachat"
_FORBIDDEN = re.compile(
    r"(?i)(authorization\s*:|bearer\s+[A-Za-z0-9\-_\.=+/]{8,}|access_token|credentials\s*[:=])"
)


@pytest.mark.unit
def test_gigachat_fixtures_have_no_secrets() -> None:
    assert _FIXTURES.is_dir()
    for path in sorted(_FIXTURES.glob("*.json")):
        text = path.read_text(encoding="utf-8")
        assert _FORBIDDEN.search(text) is None, f"secret-like token in {path.name}"
