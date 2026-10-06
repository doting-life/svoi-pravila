"""Unit tests for migrate settings and Grafana reader provisioning helpers."""

from __future__ import annotations

import io
import sys
from unittest.mock import MagicMock, patch

import pytest
from pydantic import SecretStr, ValidationError

from svoi_pravila.bootstrap import load_migrate_settings
from svoi_pravila.cli import migrate as migrate_mod
from svoi_pravila.cli.migrate import _format_validation_errors, provision_grafana_reader
from svoi_pravila.config import GrafanaDbPasswordMissingError, MigrateSettings


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
def test_load_migrate_settings_default_grafana_db_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "SP_DATABASE_URL",
        "postgresql+asyncpg://svoi:x@127.0.0.1:5432/svoi_pravila",
    )
    monkeypatch.setenv("SP_GRAFANA_DB_PASSWORD", "reader-secret")
    monkeypatch.delenv("SP_GRAFANA_DB_USER", raising=False)
    settings = load_migrate_settings()
    assert settings.grafana_db_user == "grafana_reader"


@pytest.mark.unit
def test_load_migrate_settings_rejects_invalid_grafana_db_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "SP_DATABASE_URL",
        "postgresql+asyncpg://svoi:x@127.0.0.1:5432/svoi_pravila",
    )
    monkeypatch.setenv("SP_GRAFANA_DB_PASSWORD", "reader-secret")
    monkeypatch.setenv("SP_GRAFANA_DB_USER", "Bad-User")
    with pytest.raises(ValidationError):
        load_migrate_settings()


@pytest.mark.unit
async def test_provision_grafana_reader_rejects_blank_password() -> None:
    with pytest.raises(GrafanaDbPasswordMissingError):
        await provision_grafana_reader(MagicMock(), SecretStr("  "))


@pytest.mark.unit
def test_validation_error_stderr_omits_database_url_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    leak = "SuperSecretPass123Leak"
    monkeypatch.setenv(
        "SP_DATABASE_URL",
        f"postgresql+asyncpg://u:{leak}@127.0.0.1:5432/svoi_pravila",
    )
    monkeypatch.setenv("SP_GRAFANA_DB_PASSWORD", "reader-ok")
    monkeypatch.setenv("SP_GRAFANA_DB_USER", "INVALID-USER")
    stderr = io.StringIO()
    with (
        patch.object(sys, "stderr", stderr),
        pytest.raises(SystemExit) as caught,
    ):
        migrate_mod.main()
    assert caught.value.code == 2
    text = stderr.getvalue()
    assert leak not in text
    assert "grafana_db_user" in text


@pytest.mark.unit
def test_format_validation_errors_uses_locations_and_types_only() -> None:
    with pytest.raises(ValidationError) as caught:
        MigrateSettings(
            database_url="postgresql+asyncpg://u:hidden@h/db",
            grafana_db_password="ok",
            grafana_db_user="Bad-User",
        )
    formatted = _format_validation_errors(caught.value)
    assert "hidden" not in formatted
    assert "grafana_db_user" in formatted
