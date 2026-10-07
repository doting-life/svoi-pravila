"""Localization catalog startup checks."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from svoi_pravila.adapters.channels.telegram.localization import (
    firmness_label,
    help_say_intent_prefixes,
    load_ru_strings,
    render_crisis_message,
    render_refuse_manipulation,
)
from svoi_pravila.domain.enums import Firmness


@pytest.mark.unit
def test_load_ru_strings_succeeds() -> None:
    strings = load_ru_strings()
    assert strings.dm_welcome
    assert strings.dm_open_app
    assert strings.help_inline
    assert strings.inline_prefix_decline
    assert strings.inline_button_need_support
    assert strings.inline_button_finish_setup
    assert strings.pair_invite_accepted
    assert strings.rate_limited
    assert strings.error_generic
    assert render_refuse_manipulation(strings) == strings.decode_refuse_manipulation
    assert "112" in render_crisis_message()
    for _prefix, _intent in help_say_intent_prefixes(strings):
        assert _prefix
    assert firmness_label(strings, Firmness.GENTLE) == strings.inline_firmness_gentle
    assert firmness_label(strings, Firmness.BALANCED) == strings.inline_firmness_balanced
    assert firmness_label(strings, Firmness.FIRM) == strings.inline_firmness_firm


@pytest.mark.unit
def test_load_ru_strings_missing_key_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    broken = tmp_path / "ru.json"
    broken.write_text(json.dumps({"dm.welcome": "x"}), encoding="utf-8")

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
