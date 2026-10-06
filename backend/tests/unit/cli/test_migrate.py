"""Unit tests for migrate settings and Grafana reader provisioning helpers."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from pydantic import SecretStr, ValidationError

from svoi_pravila.bootstrap import load_migrate_settings
from svoi_pravila.cli.migrate import provision_grafana_reader
from svoi_pravila.config import GrafanaDbPasswordMissingError


@pytest.mark.unit
def test_grafana_db_password_missing_is_typed() -> None:
    assert issubclass(GrafanaDbPasswordMissingError, Exception)
    assert str(GrafanaDbPasswordMissingError())


@pytest.mark.unit
def test_load_migrate_settings_requires_grafana_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "SP_DATABASE_URL",
        "postgresql+asyncpg://svoi:x@127.0.0.1:5432/svoi_pravila",
    )
    monkeypatch.delenv("SP_GRAFANA_DB_PASSWORD", raising=False)
    with pytest.raises(GrafanaDbPasswordMissingError):
        load_migrate_settings()


@pytest.mark.unit
def test_load_migrate_settings_rejects_blank_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "SP_DATABASE_URL",
        "postgresql+asyncpg://svoi:x@127.0.0.1:5432/svoi_pravila",
    )
    monkeypatch.setenv("SP_GRAFANA_DB_PASSWORD", "   ")
    with pytest.raises((GrafanaDbPasswordMissingError, ValidationError)):
        load_migrate_settings()


@pytest.mark.unit
async def test_provision_grafana_reader_rejects_blank_password() -> None:
    with pytest.raises(GrafanaDbPasswordMissingError):
        await provision_grafana_reader(MagicMock(), SecretStr("  "))
