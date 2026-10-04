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


def _unauthorized() -> Response:
    return Response(status_code=status.HTTP_404_NOT_FOUND)


def _bad_request() -> Response:
    return PlainTextResponse(status_code=status.HTTP_400_BAD_REQUEST, content="")


def _authorize(
    bindings: TelegramWebhookBindings,
    path_secret_param: str,
    header_token: str | None,
) -> Response | None:
    if not hmac.compare_digest(path_secret_param, bindings.path_secret):
        return _unauthorized()
    token = header_token or ""
    if not hmac.compare_digest(token, bindings.secret_token):
        return _unauthorized()
    return None


def _parse_update(bindings: TelegramWebhookBindings, body: bytes) -> Update | Response:
    if len(body) > _MAX_BODY_BYTES:
        return Response(status_code=status.HTTP_413_CONTENT_TOO_LARGE)
    try:
        payload: Any = json.loads(body)
    except JSONDecodeError:
        return _bad_request()
    if not isinstance(payload, dict):
        return _bad_request()
    try:
        return Update.model_validate(payload, context={"bot": bindings.parse_bot})
    except ValidationError:
        return _bad_request()


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

        auth_error = _authorize(bindings, path_secret_param, x_telegram_bot_api_secret_token)
        if auth_error is not None:
            return auth_error

        parsed = _parse_update(bindings, await request.body())
        if isinstance(parsed, Response):
            return parsed

        async def _process() -> None:
            await bindings.feed_update(parsed)

        bindings.schedule(_process())
        return Response(status_code=status.HTTP_200_OK)

    return router
