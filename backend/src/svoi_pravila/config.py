"""Application settings — the only module that reads environment variables."""

from __future__ import annotations

from enum import StrEnum
from typing import Self

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


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
