"""Shared privacy catalog loader and cross-surface equality."""

from __future__ import annotations

import pytest

from svoi_pravila.adapters.channels.telegram.localization import load_ru_strings
from svoi_pravila.privacy import load_privacy_catalog
from svoi_pravila.privacy.catalog import PrivacyCatalog, _load_action, _load_export


@pytest.mark.unit
def test_load_privacy_catalog_shape() -> None:
    catalog = load_privacy_catalog()
    assert isinstance(catalog, PrivacyCatalog)
    assert catalog.export.description
    assert catalog.export.sections
    assert catalog.revoke.description
    assert catalog.revoke.confirm
    assert catalog.delete.description
    assert catalog.delete.confirm
    assert "Подтвердите отзыв." in catalog.revoke.confirm
    assert "Подтвердите отзыв." not in catalog.revoke.description


@pytest.mark.unit
def test_bot_rights_strings_match_privacy_catalog() -> None:
    catalog = load_privacy_catalog()
    strings = load_ru_strings()
    assert strings.rights_export_caption == catalog.export.description
    assert strings.rights_revoke_explain == catalog.revoke.confirm
    assert strings.rights_delete_explain == catalog.delete.description


@pytest.mark.unit
def test_catalog_loader_rejects_malformed_blocks() -> None:
    with pytest.raises(TypeError):
        _load_export(None)
    with pytest.raises(TypeError):
        _load_export({"description": "", "sections": {"a": "b"}})
    with pytest.raises(TypeError):
        _load_export({"description": "ok", "sections": {}})
    with pytest.raises(TypeError):
        _load_export({"description": "ok", "sections": {"a": ""}})
    with pytest.raises(TypeError):
        _load_action(None, label="revoke")
    with pytest.raises(TypeError):
        _load_action({"description": "ok", "confirm": ""}, label="revoke")
