"""Shared test factories."""

from __future__ import annotations

from svoi_pravila.config import Environment, LogLevel, Settings


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
    }
    values.update(overrides)
    return Settings.model_validate(values)
