"""Unit tests for the shared limits catalog loader."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from svoi_pravila.limits.catalog import format_reset_hhmm, load_limits_catalog


@pytest.mark.unit
def test_load_limits_catalog_and_format() -> None:
    catalog = load_limits_catalog()
    assert "{reset_time}" in catalog.user_quota
    assert "{reset_time}" in catalog.service_budget
    reset = format_reset_hhmm(datetime(2026, 3, 16, 21, 0, tzinfo=UTC), "Europe/Moscow")
    assert reset == "00:00"
    assert reset in catalog.user_quota_message(reset)
    assert reset in catalog.service_budget_message(reset)


@pytest.mark.unit
def test_load_limits_catalog_rejects_non_object(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Path:
        def read_text(self, encoding: str = "utf-8") -> str:
            _ = encoding
            return "[1, 2]"

    class _Files:
        def joinpath(self, _name: str) -> _Path:
            return _Path()

    monkeypatch.setattr(
        "svoi_pravila.limits.catalog.resources.files",
        lambda _pkg: _Files(),
    )
    with pytest.raises(TypeError, match="JSON object"):
        load_limits_catalog()


@pytest.mark.unit
def test_load_limits_catalog_rejects_empty_and_missing_placeholder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Empty:
        def read_text(self, encoding: str = "utf-8") -> str:
            _ = encoding
            return '{"user_quota": "", "service_budget": "x {reset_time}"}'

    class _Missing:
        def read_text(self, encoding: str = "utf-8") -> str:
            _ = encoding
            return '{"user_quota": "no placeholder", "service_budget": "ok {reset_time}"}'

    class _Files:
        def __init__(self, path: object) -> None:
            self._path = path

        def joinpath(self, _name: str) -> object:
            return self._path

    monkeypatch.setattr(
        "svoi_pravila.limits.catalog.resources.files",
        lambda _pkg: _Files(_Empty()),
    )
    with pytest.raises(TypeError, match="non-empty"):
        load_limits_catalog()

    monkeypatch.setattr(
        "svoi_pravila.limits.catalog.resources.files",
        lambda _pkg: _Files(_Missing()),
    )
    with pytest.raises(TypeError, match="reset_time"):
        load_limits_catalog()
