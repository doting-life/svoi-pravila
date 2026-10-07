"""ValkeyQuotaGate concurrency and idempotent refund."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
from redis.asyncio import Redis

from svoi_pravila.adapters.cache.quota_gate import ValkeyQuotaGate
from svoi_pravila.application.ports.quota_gate import QuotaExhausted, Reserved
from svoi_pravila.domain.enums import QuotaClass
from svoi_pravila.domain.product_day import product_day

_PSEUDO = "a" * 64
_TZ = "Europe/Moscow"
_DAY = product_day(datetime.now(UTC), _TZ)


@pytest.mark.integration
async def test_quota_gate_concurrent_reserves_honor_limit(valkey_db15: Redis) -> None:
    gate = ValkeyQuotaGate(
        valkey_db15,
        inline_limit=40,
        decode_limit=40,
        timezone=_TZ,
    )

    async def one() -> Reserved | QuotaExhausted:
        return await gate.reserve(_PSEUDO, QuotaClass.DECODE, _DAY)

    results = await asyncio.gather(*[one() for _ in range(100)])
    reserved = [item for item in results if isinstance(item, Reserved)]
    exhausted = [item for item in results if isinstance(item, QuotaExhausted)]
    assert len(reserved) == 40
    assert len(exhausted) == 60

    first = reserved[0]
    await gate.refund(first.reservation)
    await gate.refund(first.reservation)
    again = await gate.reserve(_PSEUDO, QuotaClass.DECODE, _DAY)
    assert isinstance(again, Reserved)
    blocked = await gate.reserve(_PSEUDO, QuotaClass.DECODE, _DAY)
    assert isinstance(blocked, QuotaExhausted)
