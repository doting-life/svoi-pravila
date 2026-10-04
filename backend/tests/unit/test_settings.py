"""Settings validation tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from svoi_pravila.config import (
    DatabaseSettings,
    Environment,
    LogLevel,
    Settings,
    TelegramUpdatesMode,
)
from svoi_pravila.config import TestInfraSettings as InfraEnvSettings
from tests.factories import make_settings

_CERT = Path(__file__).resolve().parents[2] / "certs" / "russian_trusted_root_ca.pem"
_PEPPER = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
_TOKEN = "123456:ABC-DEF"
_PATH_SECRET = "a" * 32
_SECRET_TOKEN = "b" * 32


@pytest.mark.unit
def test_settings_valid() -> None:
    settings = make_settings()
    assert settings.environment is Environment.TEST
    assert settings.telegram_updates_mode is TelegramUpdatesMode.DISABLED
    assert settings.database_url.get_secret_value().startswith("postgresql+asyncpg://")
    assert settings.forwarded_allow_ips == "127.0.0.1"
    assert len(settings.pseudonym_pepper_bytes()) == 32


@pytest.mark.unit
def test_settings_rejects_missing_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SP_ENVIRONMENT", raising=False)
    values = make_settings().model_dump()
    del values["environment"]
    with pytest.raises(ValidationError):
        Settings.model_validate(values)


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


def _set_base_env(monkeypatch: pytest.MonkeyPatch) -> None:
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
    monkeypatch.setenv("SP_GIGACHAT_CREDENTIALS", "test-credentials")
    monkeypatch.setenv("SP_GIGACHAT_SCOPE", "PERS")
    monkeypatch.setenv("SP_GIGACHAT_CA_BUNDLE_FILE", str(_CERT))
    monkeypatch.setenv("SP_GIGACHAT_MODEL_SOFTEN", "GigaChat-2")
    monkeypatch.setenv("SP_GIGACHAT_MODEL_HELP_SAY", "GigaChat-2")
    monkeypatch.setenv("SP_GIGACHAT_MODEL_DECODE", "GigaChat-2")
    monkeypatch.setenv("SP_GIGACHAT_TIMEOUT_SECONDS", "5")
    monkeypatch.setenv("SP_GIGACHAT_MAX_RETRIES", "0")
    monkeypatch.setenv("SP_PSEUDONYM_PEPPER", _PEPPER)
    monkeypatch.setenv("SP_TELEGRAM_UPDATES_MODE", "disabled")


@pytest.mark.unit
def test_settings_from_env_requires_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SP_ENVIRONMENT", raising=False)
    _set_base_env(monkeypatch)
    with pytest.raises(ValidationError):
        Settings()


@pytest.mark.unit
def test_settings_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SP_ENVIRONMENT", "test")
    _set_base_env(monkeypatch)
    monkeypatch.setenv("SP_FORWARDED_ALLOW_IPS", "10.0.0.1")
    settings = Settings()
    assert settings.environment is Environment.TEST
    assert settings.readiness_timeout_seconds == 1.5
    assert settings.forwarded_allow_ips == "10.0.0.1"
    assert len(settings.data_kek_bytes()) == 32
    assert settings.telegram_updates_mode is TelegramUpdatesMode.DISABLED


@pytest.mark.unit
def test_settings_rejects_bad_data_kek() -> None:
    with pytest.raises(ValidationError):
        make_settings(data_kek="not-base64!!!")
    with pytest.raises(ValidationError):
        make_settings(data_kek="AAAA")  # too short
    with pytest.raises(ValidationError):
        make_settings(data_kek_id="BAD_ID")


@pytest.mark.unit
def test_settings_rejects_missing_gigachat_ca_bundle() -> None:
    with pytest.raises(ValidationError):
        make_settings(gigachat_ca_bundle_file=Path("/nonexistent/russian_trusted_root_ca.pem"))


@pytest.mark.unit
def test_settings_rejects_weak_pseudonym_pepper() -> None:
    with pytest.raises(ValidationError):
        make_settings(pseudonym_pepper="AAAA")
    with pytest.raises(ValidationError):
        make_settings(pseudonym_pepper="not-base64!!!")


@pytest.mark.unit
def test_disabled_mode_only_in_test() -> None:
    make_settings(environment=Environment.TEST, telegram_updates_mode=TelegramUpdatesMode.DISABLED)
    with pytest.raises(ValidationError):
        make_settings(
            environment=Environment.LOCAL,
            telegram_updates_mode=TelegramUpdatesMode.DISABLED,
        )


@pytest.mark.unit
@pytest.mark.parametrize("environment", [Environment.STAGING, Environment.PRODUCTION])
def test_staging_production_require_webhook(environment: Environment) -> None:
    with pytest.raises(ValidationError):
        make_settings(
            environment=environment,
            telegram_updates_mode=TelegramUpdatesMode.POLLING,
            telegram_bot_token=_TOKEN,
        )
    settings = make_settings(
        environment=environment,
        telegram_updates_mode=TelegramUpdatesMode.WEBHOOK,
        telegram_bot_token=_TOKEN,
        telegram_webhook_base_url="https://example.example",
        telegram_webhook_path_secret=_PATH_SECRET,
        telegram_webhook_secret_token=_SECRET_TOKEN,
    )
    assert settings.telegram_updates_mode is TelegramUpdatesMode.WEBHOOK


@pytest.mark.unit
def test_local_allows_polling_and_webhook() -> None:
    polling = make_settings(
        environment=Environment.LOCAL,
        telegram_updates_mode=TelegramUpdatesMode.POLLING,
        telegram_bot_token=_TOKEN,
    )
    assert polling.telegram_updates_mode is TelegramUpdatesMode.POLLING
    webhook = make_settings(
        environment=Environment.LOCAL,
        telegram_updates_mode=TelegramUpdatesMode.WEBHOOK,
        telegram_bot_token=_TOKEN,
        telegram_webhook_base_url="https://example.example",
        telegram_webhook_path_secret=_PATH_SECRET,
        telegram_webhook_secret_token=_SECRET_TOKEN,
    )
    assert webhook.telegram_updates_mode is TelegramUpdatesMode.WEBHOOK


@pytest.mark.unit
def test_bot_token_required_unless_disabled() -> None:
    with pytest.raises(ValidationError):
        make_settings(
            environment=Environment.LOCAL,
            telegram_updates_mode=TelegramUpdatesMode.POLLING,
            telegram_bot_token=None,
        )
    with pytest.raises(ValidationError):
        make_settings(
            environment=Environment.LOCAL,
            telegram_updates_mode=TelegramUpdatesMode.POLLING,
            telegram_bot_token=SecretStr(""),
        )


@pytest.mark.unit
def test_webhook_settings_required_in_webhook_mode() -> None:
    with pytest.raises(ValidationError):
        make_settings(
            environment=Environment.LOCAL,
            telegram_updates_mode=TelegramUpdatesMode.WEBHOOK,
            telegram_bot_token=_TOKEN,
        )


@pytest.mark.unit
def test_webhook_base_url_must_be_https() -> None:
    with pytest.raises(ValidationError):
        make_settings(
            environment=Environment.LOCAL,
            telegram_updates_mode=TelegramUpdatesMode.WEBHOOK,
            telegram_bot_token=_TOKEN,
            telegram_webhook_base_url="http://example.example",
            telegram_webhook_path_secret=_PATH_SECRET,
            telegram_webhook_secret_token=_SECRET_TOKEN,
        )


@pytest.mark.unit
def test_webhook_path_secret_constraints() -> None:
    with pytest.raises(ValidationError):
        make_settings(
            environment=Environment.LOCAL,
            telegram_updates_mode=TelegramUpdatesMode.WEBHOOK,
            telegram_bot_token=_TOKEN,
            telegram_webhook_base_url="https://example.example",
            telegram_webhook_path_secret="short",
            telegram_webhook_secret_token=_SECRET_TOKEN,
        )
    with pytest.raises(ValidationError):
        make_settings(
            environment=Environment.LOCAL,
            telegram_updates_mode=TelegramUpdatesMode.WEBHOOK,
            telegram_bot_token=_TOKEN,
            telegram_webhook_base_url="https://example.example",
            telegram_webhook_path_secret="a" * 32 + "!",
            telegram_webhook_secret_token=_SECRET_TOKEN,
        )


@pytest.mark.unit
def test_webhook_secret_token_constraints() -> None:
    with pytest.raises(ValidationError):
        make_settings(
            environment=Environment.LOCAL,
            telegram_updates_mode=TelegramUpdatesMode.WEBHOOK,
            telegram_bot_token=_TOKEN,
            telegram_webhook_base_url="https://example.example",
            telegram_webhook_path_secret=_PATH_SECRET,
            telegram_webhook_secret_token="short",
        )


@pytest.mark.unit
def test_webhook_settings_rejected_outside_webhook_mode() -> None:
    with pytest.raises(ValidationError):
        make_settings(
            environment=Environment.LOCAL,
            telegram_updates_mode=TelegramUpdatesMode.POLLING,
            telegram_bot_token=_TOKEN,
            telegram_webhook_base_url="https://example.example",
        )
    with pytest.raises(ValidationError):
        make_settings(
            environment=Environment.TEST,
            telegram_updates_mode=TelegramUpdatesMode.DISABLED,
            telegram_webhook_path_secret=_PATH_SECRET,
        )


@pytest.mark.unit
def test_rate_limit_and_lifecycle_defaults() -> None:
    settings = make_settings()
    assert settings.telegram_rate_limit_per_minute == 30
    assert settings.telegram_dedup_ttl_seconds == 300
    assert settings.telegram_shutdown_grace_seconds == 10.0
    assert settings.inline_min_chars == 8
    assert settings.inline_per_hour == 30
    assert settings.inline_deadline_seconds == 8.0
    assert settings.inline_debounce_ms == 600
    assert settings.inline_cache_seconds == 30
    assert settings.prepared_result_ttl_seconds == 600


@pytest.mark.unit
def test_database_settings_accepts_url_without_telegram(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SP_DATABASE_URL", "postgresql+asyncpg://u:p@127.0.0.1:5432/db")
    monkeypatch.setenv("SP_LOG_LEVEL", "INFO")
    monkeypatch.delenv("SP_TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.setenv("SP_TELEGRAM_UPDATES_MODE", "polling")
    loaded = DatabaseSettings()
    assert loaded.database_url.get_secret_value().startswith("postgresql+asyncpg://")


@pytest.mark.unit
def test_test_infra_settings_loads_db_and_valkey(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SP_DATABASE_URL", "postgresql+asyncpg://u:p@127.0.0.1:5432/db")
    monkeypatch.setenv("SP_VALKEY_URL", "redis://127.0.0.1:6379/0")
    monkeypatch.setenv("SP_TELEGRAM_UPDATES_MODE", "polling")
    monkeypatch.delenv("SP_TELEGRAM_BOT_TOKEN", raising=False)
    loaded = InfraEnvSettings()
    assert loaded.valkey_url.get_secret_value().startswith("redis://")
