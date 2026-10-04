"""Localization catalog startup checks."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from svoi_pravila.adapters.channels.telegram.localization import (
    load_ru_strings,
    render_crisis_message,
)


@pytest.mark.unit
def test_load_ru_strings_succeeds() -> None:
    strings = load_ru_strings()
    assert strings.age_button_yes
    assert strings.help_body
    assert "/contacts" in strings.help_body
    assert "/cancel" in strings.help_body
    assert "/contacts" in strings.done_commands
    assert strings.decode_copy
    assert strings.decode_insert
    assert strings.inline_prefix_decline
    assert strings.inline_button_need_support
    assert "112" in render_crisis_message(strings)


@pytest.mark.unit
def test_load_ru_strings_missing_key_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    broken = tmp_path / "ru.json"
    broken.write_text(json.dumps({"commands.start": "x"}), encoding="utf-8")

    class _Files:
        def joinpath(self, name: str) -> Path:
            assert name == "ru.json"
            return broken

    monkeypatch.setattr(
        "svoi_pravila.adapters.channels.telegram.localization.resources.files",
        lambda _pkg: _Files(),
    )
    with pytest.raises(KeyError, match="missing localization keys"):
        load_ru_strings()
