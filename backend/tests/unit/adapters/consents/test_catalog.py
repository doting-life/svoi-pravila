"""PackageConsentCatalog tests."""

from __future__ import annotations

import hashlib
import importlib.resources

import pytest

from svoi_pravila.adapters.consents import PackageConsentCatalog
from svoi_pravila.domain.enums import ConsentKind


@pytest.mark.unit
def test_package_consent_catalog_loads_v1() -> None:
    catalog = PackageConsentCatalog()
    requirement = catalog.current_requirement()
    for kind in ConsentKind:
        document = catalog.current_document(kind)
        assert document.kind is kind
        assert document.version == "1"
        assert document.text.strip()
        assert "TODO" not in document.text
        assert "lorem" not in document.text.lower()
        raw = (
            importlib.resources.files("svoi_pravila.adapters.consents")
            .joinpath(kind.value, "v1.txt")
            .read_bytes()
        )
        assert document.sha256.value == hashlib.sha256(raw).hexdigest()
        assert requirement.for_kind(kind).version == document.version
        assert requirement.for_kind(kind).sha256 == document.sha256
        assert "#" not in document.text
        assert "/revoke" in document.text
        assert "/delete" in document.text
        assert "/export" in document.text
    special = catalog.current_document(ConsentKind.SPECIAL_CATEGORY)
    assert "GigaChat" in special.text
    assert "ПАО Сбербанк" in special.text
