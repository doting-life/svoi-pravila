"""Alembic upgrade against the dedicated test database."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from svoi_pravila.config import Settings

BACKEND_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.integration
def test_alembic_upgrade_head(migrated_schema: Settings) -> None:
    env = os.environ.copy()
    env["SP_DATABASE_URL"] = migrated_schema.database_url.get_secret_value()
    env["SP_VALKEY_URL"] = migrated_schema.valkey_url.get_secret_value()
    result = subprocess.run(
        [
            sys.executable,
            "-W",
            "error::DeprecationWarning",
            "-m",
            "alembic",
            "upgrade",
            "head",
        ],
        cwd=BACKEND_ROOT,
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, result.stdout + result.stderr
