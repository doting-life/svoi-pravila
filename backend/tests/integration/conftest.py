"""Integration test fixtures — require compose PostgreSQL and Valkey."""

from __future__ import annotations

import pytest

from svoi_pravila.config import Settings
from tests.support.postgres import isolated_settings


@pytest.fixture
def settings() -> Settings:
    """Settings pointed at the dedicated test database and Valkey DB 15."""
    return isolated_settings()
