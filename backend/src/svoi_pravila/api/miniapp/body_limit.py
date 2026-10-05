"""ASGI body-size limit for `/api/v1` (16 KiB)."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

from svoi_pravila.api.miniapp.errors import MiniappErrorCode, error_body

Scope = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[MutableMapping[str, Any]]]
Send = Callable[[MutableMapping[str, Any]], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

MAX_BODY_BYTES = 16 * 1024
_MINIAPP_PREFIX = "/api/v1"


class BodyLimitMiddleware:
    """Reject `/api/v1` request bodies larger than 16 KiB before the app runs."""

    def __init__(self, app: ASGIApp, *, max_bytes: int = MAX_BODY_BYTES) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not str(scope.get("path", "")).startswith(_MINIAPP_PREFIX):
            await self.app(scope, receive, send)
            return

        content_length = _header_value(scope, b"content-length")
        if content_length is not None:
            try:
                length = int(content_length)
            except ValueError:
                await _send_error(send, status_code=400, code=MiniappErrorCode.VALIDATION_ERROR)
                return
            if length > self.max_bytes:
                await _send_error(send, status_code=413, code=MiniappErrorCode.BODY_TOO_LARGE)
                return

        body = await _read_body_limited(receive, self.max_bytes)
        if body is None:
            await _send_error(send, status_code=413, code=MiniappErrorCode.BODY_TOO_LARGE)
            return

        replayed = False

        async def replay_receive() -> MutableMapping[str, Any]:
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        await self.app(scope, replay_receive, send)


def _header_value(scope: Scope, name: bytes) -> str | None:
    for key, value in scope.get("headers", []):
        if key == name and isinstance(value, (bytes, bytearray)):
            return bytes(value).decode("latin-1")
    return None


async def _read_body_limited(receive: Receive, max_bytes: int) -> bytes | None:
    """Read the request body; return None if it exceeds ``max_bytes``."""
    chunks: list[bytes] = []
    size = 0
    while True:
        message = await receive()
        if message["type"] == "http.disconnect":
            break
        if message["type"] != "http.request":
            continue
        chunk = message.get("body", b"")
        size += len(chunk)
        if size > max_bytes:
            while message.get("more_body"):
                message = await receive()
                if message["type"] == "http.disconnect":
                    break
            return None
        chunks.append(chunk)
        if not message.get("more_body", False):
            break
    return b"".join(chunks)


async def _send_error(send: Send, *, status_code: int, code: MiniappErrorCode) -> None:
    payload = error_body(code).model_dump(mode="json")
    raw = json.dumps(payload).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": status_code,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(raw)).encode("ascii")),
                (b"cache-control", b"no-store"),
            ],
        }
    )
    await send({"type": "http.response.body", "body": raw})
