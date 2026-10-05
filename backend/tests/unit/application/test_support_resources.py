"""Unit tests for C0 crisis/decode copy loaders."""

from __future__ import annotations

import pytest

from svoi_pravila.application.support_resources import (
    load_applied_rule_template,
    load_crisis_lead,
    load_support_resources,
)


@pytest.mark.unit
def test_loaders_return_canonical_ru_v1() -> None:
    lead = load_crisis_lead()
    assert "Ниже — контакты служб." in lead
    assert load_support_resources()
    assert load_applied_rule_template() == "Учтено правило от {date}: «{text}»"


@pytest.mark.unit
def test_load_crisis_lead_rejects_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Path:
        def read_text(self, encoding: str = "utf-8") -> str:
            _ = encoding
            return "# comment only\n\n"

    class _Files:
        def joinpath(self, _name: str) -> _Path:
            return _Path()

    monkeypatch.setattr(
        "svoi_pravila.application.support_resources.resources.files",
        lambda _pkg: _Files(),
    )
    with pytest.raises(ValueError, match="has no lead text"):
        load_crisis_lead()


@pytest.mark.unit
def test_load_applied_rule_template_rejects_wrong_line_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Path:
        def read_text(self, encoding: str = "utf-8") -> str:
            _ = encoding
            return "one\ntwo\n"

    class _Files:
        def joinpath(self, _name: str) -> _Path:
            return _Path()

    monkeypatch.setattr(
        "svoi_pravila.application.support_resources.resources.files",
        lambda _pkg: _Files(),
    )
    with pytest.raises(ValueError, match="exactly one template line"):
        load_applied_rule_template()
