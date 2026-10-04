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


class Settings(BaseSettings):
    """Runtime configuration loaded from `SP_*` process environment variables."""

    model_config = SettingsConfigDict(
        env_prefix="SP_",
        extra="forbid",
    )

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

    @field_validator("database_url")
    @classmethod
    def database_url_must_use_asyncpg(cls, value: SecretStr) -> SecretStr:
        """Reject database URLs that are not `postgresql+asyncpg://`."""
        raw = value.get_secret_value()
        if not raw.startswith("postgresql+asyncpg://"):
            msg = "database_url must start with postgresql+asyncpg://"
            raise ValueError(msg)
        return value

    @field_validator("valkey_url")
    @classmethod
    def valkey_url_must_use_redis_scheme(cls, value: SecretStr) -> SecretStr:
        """Reject Valkey URLs that are not `redis://` or `rediss://`."""
        raw = value.get_secret_value()
        if not (raw.startswith("redis://") or raw.startswith("rediss://")):
            msg = "valkey_url must start with redis:// or rediss://"
            raise ValueError(msg)
        return value

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

    def data_kek_bytes(self) -> bytes:
        """Return the decoded 32-byte KEK."""
        return base64.b64decode(self.data_kek.get_secret_value(), validate=True)

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
