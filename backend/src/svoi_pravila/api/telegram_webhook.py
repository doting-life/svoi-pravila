"""Telegram webhook HTTP endpoint."""

from __future__ import annotations

import hmac
import json
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass
from json import JSONDecodeError
from typing import Any

from aiogram import Bot
from aiogram.types import Update
from fastapi import APIRouter, Header, Request, Response, status
from fastapi.responses import PlainTextResponse
from pydantic import ValidationError

_MAX_BODY_BYTES = 256 * 1024


@dataclass(frozen=True, slots=True)
class TelegramWebhookBindings:
    """Callbacks the composition root supplies to the webhook route."""

    is_accepting: Callable[[], bool]
    path_secret: str
    secret_token: str
    parse_bot: Bot
    feed_update: Callable[[Update], Awaitable[None]]
    schedule: Callable[[Coroutine[Any, Any, None]], None]


def build_telegram_webhook_router(bindings: TelegramWebhookBindings) -> APIRouter:
    """Create the webhook router bound to composition-root callbacks."""
    router = APIRouter()

    @router.post("/telegram/webhook/{path_secret_param}")
    async def telegram_webhook(
        path_secret_param: str,
        request: Request,
        x_telegram_bot_api_secret_token: str | None = Header(default=None),
    ) -> Response:
        if not bindings.is_accepting():
            return Response(status_code=status.HTTP_503_SERVICE_UNAVAILABLE)

        if not hmac.compare_digest(path_secret_param, bindings.path_secret):
            return Response(status_code=status.HTTP_404_NOT_FOUND)
        header_token = x_telegram_bot_api_secret_token or ""
        if not hmac.compare_digest(header_token, bindings.secret_token):
            return Response(status_code=status.HTTP_404_NOT_FOUND)

        body = await request.body()
        if len(body) > _MAX_BODY_BYTES:
            return Response(status_code=status.HTTP_413_CONTENT_TOO_LARGE)

        try:
            payload: Any = json.loads(body)
        except JSONDecodeError:
            return PlainTextResponse(status_code=status.HTTP_400_BAD_REQUEST, content="")
        if not isinstance(payload, dict):
            return PlainTextResponse(status_code=status.HTTP_400_BAD_REQUEST, content="")

        try:
            update = Update.model_validate(payload, context={"bot": bindings.parse_bot})
        except ValidationError:
            return PlainTextResponse(status_code=status.HTTP_400_BAD_REQUEST, content="")

        async def _process() -> None:
            await bindings.feed_update(update)

        bindings.schedule(_process())
        return Response(status_code=status.HTTP_200_OK)

    return router
