"""Valkey-backed per-user daily quota gate (ADR-0009)."""

from __future__ import annotations

import secrets
from datetime import date

from redis.asyncio import Redis

from svoi_pravila.adapters.cache._lua import load_lua
from svoi_pravila.adapters.cache._redis_map import map_redis
from svoi_pravila.application.ports.quota_gate import (
    QuotaExhausted,
    QuotaReservation,
    Reserved,
)
from svoi_pravila.domain.enums import QuotaClass
from svoi_pravila.domain.product_day import expire_at_utc, resets_at_utc


def _expire_unix(day: date, timezone: str) -> int:
    """Unix seconds for EXPIREAT at the product-day boundary."""
    return int(expire_at_utc(day, timezone).timestamp())


class ValkeyQuotaGate:
    """Atomic reserve/refund with product-day EXPIREAT and idempotent refunds."""

    def __init__(
        self,
        client: Redis,
        *,
        inline_limit: int,
        decode_limit: int,
        timezone: str,
        key_prefix: str = "quota",
    ) -> None:
        self._client = client
        self._limits = {
            QuotaClass.INLINE: inline_limit,
            QuotaClass.DECODE: decode_limit,
        }
        self._timezone = timezone
        self._key_prefix = key_prefix
        self._reserve = client.register_script(load_lua("quota_reserve.lua"))
        self._refund = client.register_script(load_lua("quota_refund.lua"))

    def _counter_key(self, pseudonym: str, quota_class: QuotaClass, day: date) -> str:
        return f"{self._key_prefix}:{quota_class.value}:{day.isoformat()}:{pseudonym}"

    def _reservation_key(self, reservation_id: str) -> str:
        return f"{self._key_prefix}:res:{reservation_id}"

    async def reserve(
        self, pseudonym: str, quota_class: QuotaClass, day: date
    ) -> Reserved | QuotaExhausted:
        """INCR while below the class limit; set EXPIREAT one hour after day end."""
        limit = self._limits[quota_class]
        expire_unix = _expire_unix(day, self._timezone)
        resets_at = resets_at_utc(day, self._timezone)
        reservation_id = secrets.token_hex(16)
        counter = self._counter_key(pseudonym, quota_class, day)
        marker = self._reservation_key(reservation_id)

        async def _call() -> list[int]:
            raw = await self._reserve(
                keys=[counter, marker],
                args=[limit, expire_unix],
            )
            return [int(raw[0]), int(raw[1]), int(raw[2])]

        allowed, remaining_or_count, _expire = await map_redis(_call)
        resets = resets_at.replace(microsecond=0)
        if allowed != 1:
            return QuotaExhausted(resets_at=resets)
        reservation = QuotaReservation(
            reservation_id=reservation_id,
            pseudonym=pseudonym,
            quota_class=quota_class,
            day=day,
            resets_at=resets,
        )
        return Reserved(
            remaining=remaining_or_count,
            resets_at=resets,
            reservation=reservation,
        )

    async def refund(self, reservation: QuotaReservation) -> None:
        """Return one slot; never below zero; idempotent per reservation id."""
        counter = self._counter_key(reservation.pseudonym, reservation.quota_class, reservation.day)
        marker = self._reservation_key(reservation.reservation_id)

        async def _call() -> object:
            return await self._refund(keys=[counter, marker], args=[])

        await map_redis(_call)
