"""Unit tests for mini-app API Russian error catalog."""

from __future__ import annotations

import pytest

from svoi_pravila.api.miniapp.errors import MiniappErrorCode, error_body
from svoi_pravila.api.miniapp.localization import load_ru_messages


@pytest.mark.unit
def test_ru_catalog_covers_every_error_code() -> None:
    messages = load_ru_messages()
    for code in MiniappErrorCode:
        assert code.value in messages
        assert error_body(code).message == messages[code.value]


@pytest.mark.unit
def test_error_body_missing_message_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "svoi_pravila.api.miniapp.errors.load_ru_messages",
        lambda: {},
    )
    with pytest.raises(KeyError, match="unauthorized"):
        error_body(MiniappErrorCode.UNAUTHORIZED)
