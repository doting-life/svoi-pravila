"""Shared test factories."""

from __future__ import annotations

from pathlib import Path

from svoi_pravila.config import Environment, GigaChatScope, LogLevel, Settings, TelegramUpdatesMode

# Fixed test secrets (32 zero bytes, base64) — never used outside tests.
_TEST_DATA_KEK = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
_TEST_PEPPER = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
_CERT = Path(__file__).resolve().parents[1] / "certs" / "russian_trusted_root_ca.pem"


def make_settings(**overrides: object) -> Settings:
    """Build Settings for tests with safe defaults and optional overrides."""
    values: dict[str, object] = {
        "environment": Environment.TEST,
        "log_level": LogLevel.INFO,
        "http_host": "127.0.0.1",
        "http_port": 8000,
        "database_url": "postgresql+asyncpg://user:pass@127.0.0.1:5432/db",
        "valkey_url": "redis://127.0.0.1:6379/0",
        "readiness_timeout_seconds": 1.0,
        "forwarded_allow_ips": "127.0.0.1",
        "data_kek": _TEST_DATA_KEK,
        "data_kek_id": "test-1",
        "gigachat_credentials": "test-credentials",
        "gigachat_scope": GigaChatScope.PERS,
        "gigachat_ca_bundle_file": _CERT,
        "gigachat_model_soften": "GigaChat-2",
        "gigachat_model_help_say": "GigaChat-2",
        "gigachat_model_decode": "GigaChat-2",
        "gigachat_timeout_seconds": 5.0,
        "gigachat_max_retries": 0,
        "telegram_updates_mode": TelegramUpdatesMode.DISABLED,
        "telegram_bot_token": None,
        "telegram_webhook_base_url": None,
        "telegram_webhook_path_secret": None,
        "telegram_webhook_secret_token": None,
        "pseudonym_pepper": _TEST_PEPPER,
        "miniapp_url": None,
        "analytics_jobs_enabled": False,
    }
    values.update(overrides)
    return Settings.model_validate(values)
