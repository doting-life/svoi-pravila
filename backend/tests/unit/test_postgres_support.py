"""Unit tests for PostgreSQL test-support guards."""

from __future__ import annotations

import pytest

from tests.support.postgres import require_test_database_url


@pytest.mark.unit
def test_require_test_database_url_accepts_test_suffix() -> None:
    url = "postgresql+asyncpg://user:pass@127.0.0.1:5432/svoi_pravila_test"
    assert require_test_database_url(url) == url


@pytest.mark.unit
def test_require_test_database_url_rejects_manual_database() -> None:
    url = "postgresql+asyncpg://user:pass@127.0.0.1:5432/svoi_pravila"
    with pytest.raises(ValueError, match="refuses non-test database"):
        require_test_database_url(url)
