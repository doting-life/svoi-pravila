"""Application settings — the only module that reads environment variables."""

from __future__ import annotations

import base64
import binascii
import re
from enum import StrEnum
from pathlib import Path
from typing import Self

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


class DatabaseSettings(BaseSettings):
    """Migrate-process settings: database URL and logging only."""

    model_config = _SETTINGS_CONFIG

    log_level: LogLevel = LogLevel.INFO
    database_url: SecretStr

    @field_validator("database_url")
    @classmethod
    def database_url_must_use_asyncpg(cls, value: SecretStr) -> SecretStr:
        """Reject database URLs that are not `postgresql+asyncpg://`."""
        return _validate_database_url(value)


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
    decode_per_hour: int = Field(default=20, ge=1, le=10_000)
    telegram_draft_min_interval_ms: int = Field(default=500, ge=50, le=5_000)

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

    @field_validator("gigachat_ca_bundle_file")
    @classmethod
    def gigachat_ca_bundle_must_exist(cls, value: Path) -> Path:
        """Reject missing CA bundle files."""
        if not value.is_file():
            msg = f"gigachat_ca_bundle_file does not exist: {value}"
            raise ValueError(msg)
        return value

    @field_validator("pseudonym_pepper")
    @classmethod
    def pseudonym_pepper_must_be_strong_base64(cls, value: SecretStr) -> SecretStr:
        """Reject pepper values that are not base64 of at least 32 bytes."""
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
        if mode is not TelegramUpdatesMode.DISABLED and (
            token is None or not token.get_secret_value()
        ):
            msg = "telegram_bot_token is required unless telegram_updates_mode=disabled"
            raise ValueError(msg)

        webhook_fields = (
            self.telegram_webhook_base_url,
            self.telegram_webhook_path_secret,
            self.telegram_webhook_secret_token,
        )
        webhook_present = any(
            field is not None and (not isinstance(field, SecretStr) or field.get_secret_value())
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
        if path_secret is None:
            msg = "telegram_webhook_path_secret is required in webhook mode"
            raise ValueError(msg)
        path_raw = path_secret.get_secret_value()
        if len(path_raw) < _WEBHOOK_PATH_SECRET_MIN or _URL_SAFE_RE.fullmatch(path_raw) is None:
            msg = (
                "telegram_webhook_path_secret must be at least "
                f"{_WEBHOOK_PATH_SECRET_MIN} url-safe characters"
            )
            raise ValueError(msg)

        secret_token = self.telegram_webhook_secret_token
        if secret_token is None:
            msg = "telegram_webhook_secret_token is required in webhook mode"
            raise ValueError(msg)
        token_raw = secret_token.get_secret_value()
        if _WEBHOOK_SECRET_TOKEN_RE.fullmatch(token_raw) is None:
            msg = "telegram_webhook_secret_token must be 32-256 characters of [A-Za-z0-9_-]"
            raise ValueError(msg)
