"""Aiogram middleware that records Telegram update counters and durations."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Update
from aiogram.types.update import UpdateTypeLookupError

from svoi_pravila.observability.metrics import families
from svoi_pravila.observability.metrics.labels import (
    telegram_result_label,
    update_type_label,
)


def _update_type(update: Update | None) -> str:
    if update is None:
        return update_type_label(None)
    try:
        return update_type_label(update.event_type)
    except UpdateTypeLookupError:
        return update_type_label(None)


class TelegramMetricsMiddleware(BaseMiddleware):
    """Observe ``sp_telegram_updates_total`` and duration per update type."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        update = event if isinstance(event, Update) else data.get("event_update")
        update_type = _update_type(update if isinstance(update, Update) else None)
        started = time.perf_counter()
        result = "ok"
        try:
            return await handler(event, data)
        except Exception:
            result = "error"
            raise
        finally:
            families.TELEGRAM_UPDATE_DURATION.labels(update_type=update_type).observe(
                time.perf_counter() - started
            )
            families.TELEGRAM_UPDATES.labels(
                update_type=update_type,
                result=telegram_result_label(result),
            ).inc()
