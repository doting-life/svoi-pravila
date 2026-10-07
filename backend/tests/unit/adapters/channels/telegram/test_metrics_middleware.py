"""Telegram metrics middleware labels update type and result."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from aiogram.types import Chat, Message, Update, User
from prometheus_client import REGISTRY

from svoi_pravila.adapters.channels.telegram.middlewares.metrics import (
    TelegramMetricsMiddleware,
)


def _sample(metric_name: str, labels: dict[str, str]) -> float:
    for family in REGISTRY.collect():
        for sample in family.samples:
            if sample.name != metric_name:
                continue
            if all(sample.labels.get(key) == value for key, value in labels.items()):
                return float(sample.value)
    return 0.0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_telegram_metrics_middleware_ok_and_error() -> None:
    middleware = TelegramMetricsMiddleware()
    update = Update(
        update_id=1,
        message=Message(
            message_id=1,
            date=datetime(2026, 3, 1, tzinfo=UTC),
            chat=Chat(id=1, type="private"),
            from_user=User(id=1, is_bot=False, first_name="t"),
            text="x",
        ),
    )

    async def ok_handler(_event: object, _data: dict[str, Any]) -> str:
        return "ok"

    before = _sample(
        "sp_telegram_updates_total",
        {"update_type": "message", "result": "ok"},
    )
    result = await middleware(ok_handler, update, {})
    assert result == "ok"
    assert (
        _sample(
            "sp_telegram_updates_total",
            {"update_type": "message", "result": "ok"},
        )
        == before + 1.0
    )

    async def boom(_event: object, _data: dict[str, Any]) -> None:
        msg = "boom"
        raise RuntimeError(msg)

    err_before = _sample(
        "sp_telegram_updates_total",
        {"update_type": "message", "result": "error"},
    )
    with pytest.raises(RuntimeError, match="boom"):
        await middleware(boom, update, {})
    assert (
        _sample(
            "sp_telegram_updates_total",
            {"update_type": "message", "result": "error"},
        )
        == err_before + 1.0
    )
