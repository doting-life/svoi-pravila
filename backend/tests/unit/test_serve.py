"""Tests for load_settings and serve()."""

from __future__ import annotations

from pathlib import Path

import pytest

from svoi_pravila.bootstrap import load_settings, serve
from svoi_pravila.config import Environment
from tests.factories import make_settings

_CERT = Path(__file__).resolve().parents[2] / "certs" / "russian_trusted_root_ca.pem"


@pytest.mark.unit
def test_load_settings_reads_process_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SP_ENVIRONMENT", "test")
    monkeypatch.setenv("SP_LOG_LEVEL", "INFO")
    monkeypatch.setenv("SP_HTTP_HOST", "127.0.0.1")
    monkeypatch.setenv("SP_HTTP_PORT", "8000")
    monkeypatch.setenv(
        "SP_DATABASE_URL",
        "postgresql+asyncpg://user:pass@127.0.0.1:5432/db",
    )
    monkeypatch.setenv("SP_VALKEY_URL", "redis://127.0.0.1:6379/0")
    monkeypatch.setenv("SP_READINESS_TIMEOUT_SECONDS", "1.0")
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
    monkeypatch.setenv(
        "SP_PSEUDONYM_PEPPER",
        "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
    )
    monkeypatch.setenv("SP_LLM_DAILY_TOKEN_BUDGET", "20000")
    monkeypatch.setenv("SP_TELEGRAM_UPDATES_MODE", "disabled")
    monkeypatch.delenv("SP_TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("SP_TELEGRAM_WEBHOOK_BASE_URL", raising=False)
    monkeypatch.delenv("SP_TELEGRAM_WEBHOOK_PATH_SECRET", raising=False)
    monkeypatch.delenv("SP_TELEGRAM_WEBHOOK_SECRET_TOKEN", raising=False)
    settings = load_settings()
    assert settings.environment is Environment.TEST
    assert settings.forwarded_allow_ips == "127.0.0.1"


@pytest.mark.unit
def test_serve_runs_uvicorn_with_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = make_settings(http_host="127.0.0.1", http_port=9000, forwarded_allow_ips="10.0.0.1")
    app_sentinel = object()
    captured: dict[str, object] = {}

    def fake_run(app: object, **kwargs: object) -> None:
        captured["app"] = app
        captured.update(kwargs)

    monkeypatch.setattr("svoi_pravila.bootstrap.load_settings", lambda: settings)
    monkeypatch.setattr("svoi_pravila.bootstrap.create_application", lambda _s: app_sentinel)
    monkeypatch.setattr("svoi_pravila.bootstrap.uvicorn.run", fake_run)

    serve()

    assert captured["app"] is app_sentinel
    assert captured["host"] == "127.0.0.1"
    assert captured["port"] == 9000
    assert captured["proxy_headers"] is True
    assert captured["forwarded_allow_ips"] == "10.0.0.1"
    assert captured["access_log"] is False
    assert captured["log_config"] is None
