"""Telegram WebApp initData verifier (aiogram HMAC)."""

from __future__ import annotations

from datetime import UTC, timedelta

from aiogram.utils.web_app import safe_parse_webapp_init_data
from pydantic import SecretStr

from svoi_pravila.application.ports.clock import Clock
from svoi_pravila.application.ports.init_data import (
    InitDataExpired,
    InitDataInvalid,
    VerifiedInitData,
)
from svoi_pravila.domain.ids import TelegramUserId

_FUTURE_SKEW = timedelta(seconds=60)


class AiogramInitDataVerifier:
    """Validate initData with aiogram and enforce auth_date freshness via Clock."""

    def __init__(
        self,
        bot_token: SecretStr,
        clock: Clock,
        *,
        max_age_seconds: int,
    ) -> None:
        self._bot_token = bot_token
        self._clock = clock
        self._max_age = timedelta(seconds=max_age_seconds)

    def verify(self, raw: str) -> VerifiedInitData:
        """Verify signature, require user id, check freshness; drop all other fields."""
        if not raw.strip():
            raise InitDataInvalid()
        token = self._bot_token.get_secret_value()
        try:
            parsed = safe_parse_webapp_init_data(token, raw)
        except ValueError as exc:
            raise InitDataInvalid() from exc
        if parsed.user is None:
            raise InitDataInvalid()
        auth_date = parsed.auth_date
        if auth_date.tzinfo is None:
            auth_date = auth_date.replace(tzinfo=UTC)
        else:
            auth_date = auth_date.astimezone(UTC)
        now = self._clock.now()
        if auth_date > now + _FUTURE_SKEW:
            raise InitDataInvalid()
        if now - auth_date > self._max_age:
            raise InitDataExpired()
        return VerifiedInitData(
            telegram_user_id=TelegramUserId(parsed.user.id),
            auth_date=auth_date,
        )
