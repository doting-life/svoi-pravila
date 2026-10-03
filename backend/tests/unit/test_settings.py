"""Settings validation tests."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from svoi_pravila.config import Environment, LogLevel, Settings
from tests.factories import make_settings


@pytest.mark.unit
def test_settings_valid() -> None:
    settings = make_settings()
    assert settings.environment is Environment.TEST
    assert settings.database_url.get_secret_value().startswith("postgresql+asyncpg://")
    assert settings.forwarded_allow_ips == "127.0.0.1"


@pytest.mark.unit
def test_settings_rejects_missing_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SP_ENVIRONMENT", raising=False)
    with pytest.raises(ValidationError):
        Settings(
            log_level=LogLevel.INFO,
            http_host="127.0.0.1",
            http_port=8000,
            database_url="postgresql+asyncpg://user:pass@127.0.0.1:5432/db",
            valkey_url="redis://127.0.0.1:6379/0",
            readiness_timeout_seconds=1.0,
            forwarded_allow_ips="127.0.0.1",
            data_kek="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
            data_kek_id="test-1",
        )


@pytest.mark.unit
def test_settings_rejects_wrong_database_scheme() -> None:
    with pytest.raises(ValidationError):
        make_settings(database_url="postgresql://user:pass@127.0.0.1:5432/db")


@pytest.mark.unit
def test_settings_rejects_wrong_valkey_scheme() -> None:
    with pytest.raises(ValidationError):
        make_settings(valkey_url="http://127.0.0.1:6379/0")


@pytest.mark.unit
@pytest.mark.parametrize("environment", [Environment.STAGING, Environment.PRODUCTION])
def test_settings_rejects_debug_in_staging_and_production(environment: Environment) -> None:
    with pytest.raises(ValidationError):
        make_settings(environment=environment, log_level=LogLevel.DEBUG)


@pytest.mark.unit
@pytest.mark.parametrize("timeout", [0.0, -1.0, 5.1])
def test_settings_rejects_timeout_out_of_bounds(timeout: float) -> None:
    with pytest.raises(ValidationError):
        make_settings(readiness_timeout_seconds=timeout)


@pytest.mark.unit
def test_settings_accepts_rediss_valkey_url() -> None:
    settings = make_settings(valkey_url="rediss://:pass@127.0.0.1:6379/0")
    assert settings.valkey_url.get_secret_value().startswith("rediss://")


@pytest.mark.unit
def test_settings_from_env_requires_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SP_ENVIRONMENT", raising=False)
    monkeypatch.setenv("SP_LOG_LEVEL", "INFO")
    monkeypatch.setenv("SP_HTTP_HOST", "127.0.0.1")
    monkeypatch.setenv("SP_HTTP_PORT", "8000")
    monkeypatch.setenv(
        "SP_DATABASE_URL",
        "postgresql+asyncpg://user:pass@127.0.0.1:5432/db",
    )
    monkeypatch.setenv("SP_VALKEY_URL", "redis://127.0.0.1:6379/0")
    monkeypatch.setenv("SP_READINESS_TIMEOUT_SECONDS", "1.5")
    monkeypatch.setenv("SP_FORWARDED_ALLOW_IPS", "127.0.0.1")
    monkeypatch.setenv(
        "SP_DATA_KEK",
        "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
    )
    monkeypatch.setenv("SP_DATA_KEK_ID", "test-1")
    with pytest.raises(ValidationError):
        Settings()


@pytest.mark.unit
def test_settings_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SP_ENVIRONMENT", "test")
    monkeypatch.setenv("SP_LOG_LEVEL", "INFO")
    monkeypatch.setenv("SP_HTTP_HOST", "127.0.0.1")
    monkeypatch.setenv("SP_HTTP_PORT", "8000")
    monkeypatch.setenv(
        "SP_DATABASE_URL",
        "postgresql+asyncpg://user:pass@127.0.0.1:5432/db",
    )
    monkeypatch.setenv("SP_VALKEY_URL", "redis://127.0.0.1:6379/0")
    monkeypatch.setenv("SP_READINESS_TIMEOUT_SECONDS", "1.5")
    monkeypatch.setenv("SP_FORWARDED_ALLOW_IPS", "10.0.0.1")
    monkeypatch.setenv(
        "SP_DATA_KEK",
        "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
    )
    monkeypatch.setenv("SP_DATA_KEK_ID", "test-1")
    settings = Settings()
    assert settings.environment is Environment.TEST
    assert settings.readiness_timeout_seconds == 1.5
    assert settings.forwarded_allow_ips == "10.0.0.1"
    assert len(settings.data_kek_bytes()) == 32


@pytest.mark.unit
def test_settings_rejects_bad_data_kek() -> None:
    with pytest.raises(ValidationError):
        make_settings(data_kek="not-base64!!!")
    with pytest.raises(ValidationError):
        make_settings(data_kek="AAAA")  # too short
    with pytest.raises(ValidationError):
        make_settings(data_kek_id="BAD_ID")
