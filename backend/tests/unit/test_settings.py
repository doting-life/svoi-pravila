"""Settings validation tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from svoi_pravila.bootstrap import load_settings
from svoi_pravila.config import (
    DatabaseSettings,
    Environment,
    LlmDailyTokenBudgetMissingError,
    LlmToolSettings,
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
def test_settings_rejects_invalid_display_timezone() -> None:
    with pytest.raises(ValidationError, match="IANA"):
        make_settings(display_timezone="Not/AZone")


@pytest.mark.unit
def test_settings_rejects_invalid_analytics_timezone() -> None:
    with pytest.raises(ValidationError, match="IANA"):
        make_settings(analytics_timezone="Not/AZone")


@pytest.mark.unit
def test_settings_rejects_invalid_analytics_run_at() -> None:
    with pytest.raises(ValidationError):
        make_settings(analytics_run_at="25:00")


@pytest.mark.unit
def test_settings_analytics_defaults() -> None:
    values = make_settings().model_dump()
    del values["analytics_jobs_enabled"]
    settings = Settings.model_validate(values)
    assert settings.analytics_jobs_enabled is True
    assert settings.analytics_timezone == "Europe/Moscow"
    assert settings.analytics_run_at.hour == 3
    assert settings.analytics_run_at.minute == 30


@pytest.mark.unit
def test_settings_accepts_https_miniapp_url_origin() -> None:
    settings = make_settings(miniapp_url="https://example.trycloudflare.com/")
    assert settings.miniapp_url == "https://example.trycloudflare.com"


@pytest.mark.unit
@pytest.mark.parametrize(
    "value",
    [
        "http://example.trycloudflare.com",
        "https://user:pass@example.trycloudflare.com",
        "https://example.trycloudflare.com/path",
        "https://example.trycloudflare.com?q=1",
        "https://example.trycloudflare.com#frag",
    ],
)
def test_settings_rejects_invalid_miniapp_url(value: str) -> None:
    with pytest.raises(ValidationError, match="miniapp_url"):
        make_settings(miniapp_url=value)


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
    monkeypatch.setenv("SP_LLM_DAILY_TOKEN_BUDGET", "20000")
    monkeypatch.setenv("SP_TELEGRAM_UPDATES_MODE", "disabled")
    monkeypatch.delenv("SP_TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("SP_TELEGRAM_WEBHOOK_BASE_URL", raising=False)
    monkeypatch.delenv("SP_TELEGRAM_WEBHOOK_PATH_SECRET", raising=False)
    monkeypatch.delenv("SP_TELEGRAM_WEBHOOK_SECRET_TOKEN", raising=False)


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
    assert settings.quota_inline_per_day == 300
    assert settings.quota_decode_per_day == 40
    assert settings.llm_daily_token_budget == 20_000
    assert settings.inline_deadline_seconds == 8.0
    assert settings.inline_debounce_ms == 600
    assert settings.inline_cache_seconds == 30
    assert settings.inline_reuse_max_entries == 10_000
    assert settings.prepared_result_ttl_seconds == 600


@pytest.mark.unit
def test_load_settings_requires_llm_daily_token_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SP_ENVIRONMENT", "test")
    _set_base_env(monkeypatch)
    monkeypatch.delenv("SP_LLM_DAILY_TOKEN_BUDGET", raising=False)
    with pytest.raises(LlmDailyTokenBudgetMissingError):
        load_settings()


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


@pytest.mark.unit
def test_llm_tool_settings_loads_without_telegram(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SP_GIGACHAT_CREDENTIALS", "test-credentials")
    monkeypatch.setenv("SP_GIGACHAT_SCOPE", "PERS")
    monkeypatch.setenv("SP_GIGACHAT_CA_BUNDLE_FILE", str(_CERT))
    monkeypatch.setenv("SP_GIGACHAT_MODEL_SOFTEN", "GigaChat-3-Lightning")
    monkeypatch.setenv("SP_GIGACHAT_MODEL_HELP_SAY", "GigaChat-3-Lightning")
    monkeypatch.setenv("SP_GIGACHAT_MODEL_DECODE", "GigaChat-2-Pro")
    monkeypatch.setenv("SP_TELEGRAM_UPDATES_MODE", "polling")
    monkeypatch.delenv("SP_TELEGRAM_BOT_TOKEN", raising=False)
    loaded = LlmToolSettings()
    assert loaded.gigachat_model_soften == "GigaChat-3-Lightning"
    assert loaded.gigachat_scope.value == "PERS"


@pytest.mark.unit
def test_llm_tool_settings_requires_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SP_GIGACHAT_CREDENTIALS", raising=False)
    monkeypatch.setenv("SP_GIGACHAT_SCOPE", "PERS")
    monkeypatch.setenv("SP_GIGACHAT_CA_BUNDLE_FILE", str(_CERT))
    monkeypatch.setenv("SP_GIGACHAT_MODEL_SOFTEN", "GigaChat-2")
    monkeypatch.setenv("SP_GIGACHAT_MODEL_HELP_SAY", "GigaChat-2")
    monkeypatch.setenv("SP_GIGACHAT_MODEL_DECODE", "GigaChat-2")
    with pytest.raises(ValidationError):
        LlmToolSettings()


def _llm_tool_values(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "gigachat_credentials": "test-credentials",
        "gigachat_scope": "PERS",
        "gigachat_ca_bundle_file": _CERT,
        "gigachat_model_soften": "GigaChat-2",
        "gigachat_model_help_say": "GigaChat-2",
        "gigachat_model_decode": "GigaChat-2",
    }
    values.update(overrides)
    return values


@pytest.mark.unit
@pytest.mark.parametrize("blank", ["", "   "])
def test_llm_tool_settings_rejects_blank_credentials(blank: str) -> None:
    with pytest.raises(ValidationError, match="gigachat_credentials must not be empty"):
        LlmToolSettings.model_validate(_llm_tool_values(gigachat_credentials=blank))


@pytest.mark.unit
def test_llm_tool_settings_accepts_nonempty_credentials() -> None:
    loaded = LlmToolSettings.model_validate(_llm_tool_values())
    assert loaded.gigachat_credentials.get_secret_value() == "test-credentials"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("field", "blank", "message"),
    [
        ("gigachat_credentials", "", "gigachat_credentials must not be empty"),
        ("gigachat_credentials", "   ", "gigachat_credentials must not be empty"),
        ("data_kek", "", "data_kek must not be empty"),
        ("data_kek", "   ", "data_kek must not be empty"),
        ("pseudonym_pepper", "", "pseudonym_pepper must not be empty"),
        ("pseudonym_pepper", "   ", "pseudonym_pepper must not be empty"),
    ],
)
def test_settings_rejects_blank_required_secrets(field: str, blank: str, message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        make_settings(**{field: blank})


@pytest.mark.unit
@pytest.mark.parametrize("blank", ["", "   "])
def test_bot_token_rejects_whitespace_when_required(blank: str) -> None:
    with pytest.raises(ValidationError, match="telegram_bot_token"):
        make_settings(
            environment=Environment.LOCAL,
            telegram_updates_mode=TelegramUpdatesMode.POLLING,
            telegram_bot_token=SecretStr(blank),
        )


@pytest.mark.unit
@pytest.mark.parametrize("blank", ["", "   "])
def test_webhook_secrets_reject_blank(blank: str) -> None:
    with pytest.raises(ValidationError, match="telegram_webhook_path_secret must not be empty"):
        make_settings(
            environment=Environment.LOCAL,
            telegram_updates_mode=TelegramUpdatesMode.WEBHOOK,
            telegram_bot_token=_TOKEN,
            telegram_webhook_base_url="https://example.example",
            telegram_webhook_path_secret=SecretStr(blank),
            telegram_webhook_secret_token=_SECRET_TOKEN,
        )
    with pytest.raises(ValidationError, match="telegram_webhook_secret_token must not be empty"):
        make_settings(
            environment=Environment.LOCAL,
            telegram_updates_mode=TelegramUpdatesMode.WEBHOOK,
            telegram_bot_token=_TOKEN,
            telegram_webhook_base_url="https://example.example",
            telegram_webhook_path_secret=_PATH_SECRET,
            telegram_webhook_secret_token=SecretStr(blank),
        )
