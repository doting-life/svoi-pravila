"""Application settings — the only module that reads environment variables."""

from __future__ import annotations

import base64
import binascii
import re
from datetime import time
from enum import StrEnum
from pathlib import Path
from typing import Protocol, Self
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_KEK_ID_RE = re.compile(r"^[a-z0-9-]{1,32}$")
_KEK_BYTES = 32
_PEPPER_MIN_BYTES = 32
_WEBHOOK_PATH_SECRET_MIN = 32
_WEBHOOK_SECRET_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{32,256}$")
_URL_SAFE_RE = re.compile(r"^[A-Za-z0-9_-]+$")


class Environment(StrEnum):
    """Deployed environment name."""

    LOCAL = "local"
    TEST = "test"
    STAGING = "staging"
    PRODUCTION = "production"


class LogLevel(StrEnum):
    """Structured log level."""

    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class GigaChatScope(StrEnum):
    """GigaChat API access scope selected by account type."""

    PERS = "PERS"
    B2B = "B2B"
    CORP = "CORP"

    def api_scope(self) -> str:
        """Return the SDK scope string expected by GigaChat."""
        return f"GIGACHAT_API_{self.value}"


class TelegramUpdatesMode(StrEnum):
    """How Telegram updates are delivered to the process."""

    POLLING = "polling"
    WEBHOOK = "webhook"
    DISABLED = "disabled"


_SETTINGS_CONFIG = SettingsConfigDict(
    env_prefix="SP_",
    extra="forbid",
)


def _validate_database_url(value: SecretStr) -> SecretStr:
    raw = value.get_secret_value()
    if not raw.startswith("postgresql+asyncpg://"):
        msg = "database_url must start with postgresql+asyncpg://"
        raise ValueError(msg)
    return value


def _validate_valkey_url(value: SecretStr) -> SecretStr:
    raw = value.get_secret_value()
    if not (raw.startswith("redis://") or raw.startswith("rediss://")):
        msg = "valkey_url must start with redis:// or rediss://"
        raise ValueError(msg)
    return value


def _validate_ca_bundle(value: Path) -> Path:
    if not value.is_file():
        msg = f"gigachat_ca_bundle_file does not exist: {value}"
        raise ValueError(msg)
    return value


def _require_nonempty_secret(value: SecretStr, name: str) -> SecretStr:
    if not value.get_secret_value().strip():
        msg = f"{name} must not be empty"
        raise ValueError(msg)
    return value


def _secret_is_blank(value: SecretStr | None) -> bool:
    return value is None or not value.get_secret_value().strip()


class GigaChatRuntimeSettings(Protocol):
    """GigaChat fields required by the SDK client and text generator."""

    gigachat_credentials: SecretStr
    gigachat_scope: GigaChatScope
    gigachat_ca_bundle_file: Path
    gigachat_model_soften: str
    gigachat_model_help_say: str
    gigachat_model_decode: str
    gigachat_model_suggest: str
    gigachat_timeout_seconds: float
    gigachat_max_retries: int


class LlmToolSettings(BaseSettings):
    """Bench/eval process settings: GigaChat only."""

    model_config = _SETTINGS_CONFIG

    gigachat_credentials: SecretStr
    gigachat_scope: GigaChatScope
    gigachat_ca_bundle_file: Path
    gigachat_model_soften: str = Field(min_length=1)
    gigachat_model_help_say: str = Field(min_length=1)
    gigachat_model_decode: str = Field(min_length=1)
    gigachat_model_suggest: str = Field(default="GigaChat-3-Lightning", min_length=1)
    gigachat_timeout_seconds: float = Field(default=30.0, gt=0, le=120)
    gigachat_max_retries: int = Field(default=1, ge=0, le=2)

    @field_validator("gigachat_credentials")
    @classmethod
    def gigachat_credentials_must_not_be_empty(cls, value: SecretStr) -> SecretStr:
        """Reject empty GigaChat authorization keys."""
        return _require_nonempty_secret(value, "gigachat_credentials")

    @field_validator("gigachat_ca_bundle_file")
    @classmethod
    def gigachat_ca_bundle_must_exist(cls, value: Path) -> Path:
        """Reject missing CA bundle files."""
        return _validate_ca_bundle(value)


class DatabaseSettings(BaseSettings):
    """Alembic-only settings: database URL and logging."""

    model_config = _SETTINGS_CONFIG

    log_level: LogLevel = LogLevel.INFO
    database_url: SecretStr

    @field_validator("database_url")
    @classmethod
    def database_url_must_use_asyncpg(cls, value: SecretStr) -> SecretStr:
        """Reject database URLs that are not `postgresql+asyncpg://`."""
        return _validate_database_url(value)


class GrafanaDbPasswordMissingError(Exception):
    """``SP_GRAFANA_DB_PASSWORD`` is unset or blank; no default password exists."""

    def __init__(self) -> None:
        super().__init__("SP_GRAFANA_DB_PASSWORD is required and must be non-empty")


class LlmDailyTokenBudgetMissingError(Exception):
    """``SP_LLM_DAILY_TOKEN_BUDGET`` is unset; no default budget exists."""

    def __init__(self) -> None:
        super().__init__("SP_LLM_DAILY_TOKEN_BUDGET is required and must be a positive integer")


_GRAFANA_DB_USER_RE = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


class MigrateSettings(BaseSettings):
    """Compose migrate one-shot: Alembic plus Grafana reader provisioning."""

    model_config = _SETTINGS_CONFIG

    log_level: LogLevel = LogLevel.INFO
    database_url: SecretStr
    grafana_db_password: SecretStr
    grafana_db_user: str = "grafana_reader"

    @field_validator("database_url")
    @classmethod
    def database_url_must_use_asyncpg(cls, value: SecretStr) -> SecretStr:
        """Reject database URLs that are not `postgresql+asyncpg://`."""
        return _validate_database_url(value)

    @field_validator("grafana_db_password")
    @classmethod
    def grafana_db_password_must_not_be_empty(cls, value: SecretStr) -> SecretStr:
        """Reject an empty Grafana reader password (no default)."""
        return _require_nonempty_secret(value, "grafana_db_password")

    @field_validator("grafana_db_user")
    @classmethod
    def grafana_db_user_must_be_identifier(cls, value: str) -> str:
        """Reject LOGIN role names that are not safe SQL identifiers."""
        if _GRAFANA_DB_USER_RE.fullmatch(value) is None:
            msg = "grafana_db_user must match ^[a-z_][a-z0-9_]{0,62}$"
            raise ValueError(msg)
        return value


class TestInfraSettings(BaseSettings):
    """Test-fixture settings: database and Valkey URLs only."""

    model_config = _SETTINGS_CONFIG

    database_url: SecretStr
    valkey_url: SecretStr

    @field_validator("database_url")
    @classmethod
    def database_url_must_use_asyncpg(cls, value: SecretStr) -> SecretStr:
        """Reject database URLs that are not `postgresql+asyncpg://`."""
        return _validate_database_url(value)

    @field_validator("valkey_url")
    @classmethod
    def valkey_url_must_use_redis_scheme(cls, value: SecretStr) -> SecretStr:
        """Reject Valkey URLs that are not `redis://` or `rediss://`."""
        return _validate_valkey_url(value)


class Settings(BaseSettings):
    """Full API-process configuration from `SP_*` environment variables."""

    model_config = _SETTINGS_CONFIG

    environment: Environment
    log_level: LogLevel = LogLevel.INFO
    http_host: str = "127.0.0.1"
    http_port: int = Field(default=8000, ge=1, le=65535)
    database_url: SecretStr
    valkey_url: SecretStr
    readiness_timeout_seconds: float = Field(default=2.0, gt=0, le=5)
    forwarded_allow_ips: str
    data_kek: SecretStr
    data_kek_id: str
    gigachat_credentials: SecretStr
    gigachat_scope: GigaChatScope
    gigachat_ca_bundle_file: Path
    gigachat_model_soften: str = Field(min_length=1)
    gigachat_model_help_say: str = Field(min_length=1)
    gigachat_model_decode: str = Field(min_length=1)
    gigachat_model_suggest: str = Field(default="GigaChat-3-Lightning", min_length=1)
    gigachat_timeout_seconds: float = Field(default=30.0, gt=0, le=120)
    gigachat_max_retries: int = Field(default=1, ge=0, le=2)
    telegram_updates_mode: TelegramUpdatesMode
    telegram_bot_token: SecretStr | None = None
    telegram_webhook_base_url: str | None = None
    telegram_webhook_path_secret: SecretStr | None = None
    telegram_webhook_secret_token: SecretStr | None = None
    pseudonym_pepper: SecretStr
    telegram_rate_limit_per_minute: int = Field(default=30, ge=1, le=600)
    telegram_dedup_ttl_seconds: int = Field(default=300, ge=1, le=86_400)
    telegram_shutdown_grace_seconds: float = Field(default=10.0, gt=0, le=120)
    decode_deadline_seconds: float = Field(default=45.0, gt=0, le=120)
    telegram_draft_min_interval_ms: int = Field(default=500, ge=50, le=5_000)
    inline_min_chars: int = Field(default=8, ge=1, le=64)
    quota_inline_per_day: int = Field(default=300, ge=1)
    quota_decode_per_day: int = Field(default=40, ge=1)
    llm_daily_token_budget: int = Field(gt=0)
    inline_deadline_seconds: float = Field(default=8.0, gt=0, le=30)
    inline_debounce_ms: int = Field(default=600, ge=0, le=5_000)
    inline_cache_seconds: int = Field(default=30, ge=0, le=300)
    inline_reuse_max_entries: int = Field(default=10_000, ge=1, le=1_000_000)
    prepared_result_ttl_seconds: int = Field(default=600, ge=1, le=600)
    rule_source_ttl_seconds: int = Field(default=600, ge=60, le=600)
    dialog_ttl_seconds: int = Field(default=600, ge=1, le=86_400)
    display_timezone: str = Field(default="Europe/Moscow")
    analytics_timezone: str = Field(default="Europe/Moscow")
    analytics_run_at: time = Field(default=time(3, 30))
    analytics_jobs_enabled: bool = True
    miniapp_url: str | None = None
    miniapp_initdata_max_age_seconds: int = Field(default=3600, ge=60, le=86_400)
    miniapp_requests_per_minute: int = Field(default=120, ge=1, le=600)

    @field_validator("display_timezone")
    @classmethod
    def display_timezone_must_be_iana(cls, value: str) -> str:
        """Reject names that are not IANA time zones."""
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            msg = "display_timezone must be a valid IANA time zone"
            raise ValueError(msg) from exc
        return value

    @field_validator("analytics_timezone")
    @classmethod
    def analytics_timezone_must_be_iana(cls, value: str) -> str:
        """Reject names that are not IANA time zones."""
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            msg = "analytics_timezone must be a valid IANA time zone"
            raise ValueError(msg) from exc
        return value

    @field_validator("miniapp_url")
    @classmethod
    def miniapp_url_must_be_https_origin(cls, value: str | None) -> str | None:
        """Accept only https origins with empty or root path and no query/fragment."""
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            return None
        parsed = urlparse(stripped)
        if parsed.scheme != "https":
            msg = "miniapp_url must use https"
            raise ValueError(msg)
        if not parsed.netloc or parsed.username is not None or parsed.password is not None:
            msg = "miniapp_url must include a host and no userinfo"
            raise ValueError(msg)
        if parsed.path not in {"", "/"}:
            msg = "miniapp_url must not include a path"
            raise ValueError(msg)
        if parsed.query or parsed.fragment or parsed.params:
            msg = "miniapp_url must not include query, fragment, or params"
            raise ValueError(msg)
        return f"https://{parsed.netloc}"

    @field_validator("database_url")
    @classmethod
    def database_url_must_use_asyncpg(cls, value: SecretStr) -> SecretStr:
        """Reject database URLs that are not `postgresql+asyncpg://`."""
        return _validate_database_url(value)

    @field_validator("valkey_url")
    @classmethod
    def valkey_url_must_use_redis_scheme(cls, value: SecretStr) -> SecretStr:
        """Reject Valkey URLs that are not `redis://` or `rediss://`."""
        return _validate_valkey_url(value)

    @field_validator("data_kek")
    @classmethod
    def data_kek_must_be_32_bytes_base64(cls, value: SecretStr) -> SecretStr:
        """Reject KEK values that are not base64 of exactly 32 bytes."""
        _require_nonempty_secret(value, "data_kek")
        raw = value.get_secret_value()
        try:
            decoded = base64.b64decode(raw, validate=True)
        except binascii.Error as exc:
            msg = "data_kek must be valid base64"
            raise ValueError(msg) from exc
        if len(decoded) != _KEK_BYTES:
            msg = f"data_kek must decode to exactly {_KEK_BYTES} bytes"
            raise ValueError(msg)
        return value

    @field_validator("data_kek_id")
    @classmethod
    def data_kek_id_must_match_pattern(cls, value: str) -> str:
        """Reject KEK identifiers outside the allowed pattern."""
        if _KEK_ID_RE.fullmatch(value) is None:
            msg = "data_kek_id must match ^[a-z0-9-]{1,32}$"
            raise ValueError(msg)
        return value

    @field_validator("gigachat_credentials")
    @classmethod
    def gigachat_credentials_must_not_be_empty(cls, value: SecretStr) -> SecretStr:
        """Reject empty GigaChat authorization keys."""
        return _require_nonempty_secret(value, "gigachat_credentials")

    @field_validator("gigachat_ca_bundle_file")
    @classmethod
    def gigachat_ca_bundle_must_exist(cls, value: Path) -> Path:
        """Reject missing CA bundle files."""
        return _validate_ca_bundle(value)

    @field_validator("pseudonym_pepper")
    @classmethod
    def pseudonym_pepper_must_be_strong_base64(cls, value: SecretStr) -> SecretStr:
        """Reject pepper values that are not base64 of at least 32 bytes."""
        _require_nonempty_secret(value, "pseudonym_pepper")
        raw = value.get_secret_value()
        try:
            decoded = base64.b64decode(raw, validate=True)
        except binascii.Error as exc:
            msg = "pseudonym_pepper must be valid base64"
            raise ValueError(msg) from exc
        if len(decoded) < _PEPPER_MIN_BYTES:
            msg = f"pseudonym_pepper must decode to at least {_PEPPER_MIN_BYTES} bytes"
            raise ValueError(msg)
        return value

    def data_kek_bytes(self) -> bytes:
        """Return the decoded 32-byte KEK."""
        return base64.b64decode(self.data_kek.get_secret_value(), validate=True)

    def pseudonym_pepper_bytes(self) -> bytes:
        """Return the decoded pseudonym pepper bytes."""
        return base64.b64decode(self.pseudonym_pepper.get_secret_value(), validate=True)

    @model_validator(mode="after")
    def debug_forbidden_outside_local_test(self) -> Self:
        """Refuse DEBUG logging in staging and production."""
        if (
            self.environment in {Environment.STAGING, Environment.PRODUCTION}
            and self.log_level is LogLevel.DEBUG
        ):
            msg = "log_level DEBUG is not allowed in staging or production"
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def telegram_mode_and_secrets(self) -> Self:
        """Enforce Telegram update mode rules and related secret presence."""
        mode = self.telegram_updates_mode
        env = self.environment

        if mode is TelegramUpdatesMode.DISABLED and env is not Environment.TEST:
            msg = "telegram_updates_mode=disabled is allowed only when environment=test"
            raise ValueError(msg)

        if env in {Environment.STAGING, Environment.PRODUCTION}:
            if mode is not TelegramUpdatesMode.WEBHOOK:
                msg = "staging and production require telegram_updates_mode=webhook"
                raise ValueError(msg)
        elif env is Environment.LOCAL and mode is TelegramUpdatesMode.DISABLED:
            msg = "local environment allows telegram_updates_mode polling or webhook only"
            raise ValueError(msg)

        token = self.telegram_bot_token
        if mode is not TelegramUpdatesMode.DISABLED and _secret_is_blank(token):
            msg = "telegram_bot_token is required unless telegram_updates_mode=disabled"
            raise ValueError(msg)

        webhook_fields = (
            self.telegram_webhook_base_url,
            self.telegram_webhook_path_secret,
            self.telegram_webhook_secret_token,
        )
        webhook_present = any(
            field is not None
            and (
                (isinstance(field, SecretStr) and not _secret_is_blank(field))
                or (isinstance(field, str) and field.strip())
            )
            for field in webhook_fields
        )

        if mode is TelegramUpdatesMode.WEBHOOK:
            self._require_webhook_settings()
        elif webhook_present:
            msg = "webhook settings are only allowed when telegram_updates_mode=webhook"
            raise ValueError(msg)
        return self

    def _require_webhook_settings(self) -> None:
        base = self.telegram_webhook_base_url
        if base is None or not base.strip():
            msg = "telegram_webhook_base_url is required in webhook mode"
            raise ValueError(msg)
        if not base.startswith("https://"):
            msg = "telegram_webhook_base_url must use https://"
            raise ValueError(msg)

        path_secret = self.telegram_webhook_path_secret
        if path_secret is None or _secret_is_blank(path_secret):
            msg = "telegram_webhook_path_secret must not be empty"
            raise ValueError(msg)
        path_raw = path_secret.get_secret_value()
        if len(path_raw) < _WEBHOOK_PATH_SECRET_MIN or _URL_SAFE_RE.fullmatch(path_raw) is None:
            msg = (
                "telegram_webhook_path_secret must be at least "
                f"{_WEBHOOK_PATH_SECRET_MIN} url-safe characters"
            )
            raise ValueError(msg)

        secret_token = self.telegram_webhook_secret_token
        if secret_token is None or _secret_is_blank(secret_token):
            msg = "telegram_webhook_secret_token must not be empty"
            raise ValueError(msg)
        token_raw = secret_token.get_secret_value()
        if _WEBHOOK_SECRET_TOKEN_RE.fullmatch(token_raw) is None:
            msg = "telegram_webhook_secret_token must be 32-256 characters of [A-Za-z0-9_-]"
            raise ValueError(msg)
