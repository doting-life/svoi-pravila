"""Integration test fixtures — require compose PostgreSQL and Valkey."""

from __future__ import annotations

import pytest

from svoi_pravila.bootstrap import load_settings
from svoi_pravila.config import Settings


@pytest.fixture
def settings() -> Settings:
    return load_settings()
