"""HTTP observation helpers for the LLM benchmark (fixtures + 429 capture).

Uses public httpx ``event_hooks`` so observation works with proxy mounts.
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from gigachat import GigaChat

_TOKEN_RE = re.compile(
    r"(?i)[\"']?(authorization|bearer|access_token|tok|credentials)[\"']?\s*[:=]\s*[\"']?[\w\-\.=+/]+"
)
_BEARER_RE = re.compile(r"(?i)bearer\s+\S+")
_HTTP_TOO_MANY_REQUESTS = 429

EventHook = Callable[..., Awaitable[Any]]


@dataclass(frozen=True, slots=True)
class RecordingConfig:
    """Where and how to name recorded fixture files."""

    out_dir: Path
    name_prefix: str


@dataclass(frozen=True, slots=True)
class RecordedExchange:
    """One sanitized HTTP request/response pair."""

    method: str
    url: str
    status: int
    request_body: str
    response_body: str
    headers: dict[str, str]


def sanitize_headers(headers: httpx.Headers | dict[str, str]) -> dict[str, str]:
    """Drop credential-bearing headers."""
    out: dict[str, str] = {}
    for key, value in headers.items():
        lowered = key.lower()
        if lowered in {"authorization", "rquid", "cookie", "set-cookie"} or "token" in lowered:
            out[key] = "<redacted>"
            continue
        out[key] = _BEARER_RE.sub("Bearer <redacted>", value)
    return out


def sanitize_body(text: str) -> str:
    """Redact token-shaped substrings from a body string."""
    return _TOKEN_RE.sub(r"\1=<redacted>", text)


def write_fixture(path: Path, exchange: RecordedExchange) -> None:
    """Write one sanitized request/response fixture as JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "method": exchange.method,
        "url": exchange.url,
        "status": exchange.status,
        "request_headers": sanitize_headers(exchange.headers),
        "request_body": sanitize_body(exchange.request_body),
        "response_body": sanitize_body(exchange.response_body),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def gigachat_http_clients(client: GigaChat) -> tuple[httpx.AsyncClient, httpx.AsyncClient]:
    """Return the SDK's two async HTTP clients.

    gigachat==0.2.3 exposes no public way to inject an httpx client or hooks;
    ``_aclient`` / ``_auth_aclient`` are the only attachment points.
    """
    aclient: object = client._aclient
    auth_aclient: object = client._auth_aclient
    if not isinstance(aclient, httpx.AsyncClient) or not isinstance(
        auth_aclient, httpx.AsyncClient
    ):
        msg = "GigaChat SDK clients must be httpx.AsyncClient instances"
        raise TypeError(msg)
    return aclient, auth_aclient


def _snapshot_hooks(
    client: httpx.AsyncClient,
) -> tuple[list[EventHook], list[EventHook]]:
    request_hooks = list(client.event_hooks.get("request", []))
    response_hooks = list(client.event_hooks.get("response", []))
    return request_hooks, response_hooks


def _restore_hooks(
    client: httpx.AsyncClient,
    *,
    request_hooks: list[EventHook],
    response_hooks: list[EventHook],
) -> None:
    client.event_hooks["request"] = list(request_hooks)
    client.event_hooks["response"] = list(response_hooks)


class _TeeByteStream(httpx.AsyncByteStream):
    """Forward bytes to the consumer while retaining a copy for the fixture."""

    def __init__(
        self,
        source: httpx.AsyncByteStream,
        on_complete: Callable[[bytes], None],
    ) -> None:
        self._source = source
        self._on_complete = on_complete
        self._chunks: list[bytes] = []

    async def __aiter__(self) -> AsyncIterator[bytes]:
        async for chunk in self._source:
            self._chunks.append(chunk)
            yield chunk
        self._on_complete(b"".join(self._chunks))

    async def aclose(self) -> None:
        await self._source.aclose()


@dataclass(slots=True)
class _RecordingState:
    config: RecordingConfig
    index: int = 0

    def next_path(self, request: httpx.Request) -> Path:
        self.index += 1
        parsed = urlparse(str(request.url))
        host_path = f"{parsed.netloc}{parsed.path}".replace("/", "_").replace(":", "_")
        name = (
            f"{self.config.name_prefix}_{self.index:02d}_{request.method.lower()}_{host_path}.json"
        )
        return self.config.out_dir / name

    def persist(self, request: httpx.Request, status: int, response_body: str) -> None:
        write_fixture(
            self.next_path(request),
            RecordedExchange(
                method=request.method,
                url=str(request.url),
                status=status,
                request_body=request.content.decode("utf-8", errors="replace"),
                response_body=response_body,
                headers=dict(request.headers),
            ),
        )


def _make_recording_hook(state: _RecordingState) -> EventHook:
    async def on_response(response: httpx.Response) -> None:
        request = response.request
        content_type = response.headers.get("content-type", "")
        if "event-stream" in content_type:
            inner_stream = response.stream
            if not isinstance(inner_stream, httpx.AsyncByteStream):
                msg = "SSE response must be an async byte stream"
                raise TypeError(msg)

            def on_complete(body: bytes) -> None:
                state.persist(
                    request,
                    response.status_code,
                    body.decode("utf-8", errors="replace"),
                )

            # Replace the response stream with a tee so the caller still
            # receives chunks while we retain a copy for the fixture.
            response.stream = _TeeByteStream(inner_stream, on_complete)
            return
        await response.aread()
        state.persist(
            request,
            response.status_code,
            response.content.decode("utf-8", errors="replace"),
        )

    return on_response


def install_recording(client: GigaChat, config: RecordingConfig) -> Callable[[], Awaitable[None]]:
    """Install fixture recording via httpx response event hooks. Returns uninstall.

    Streamed responses are teed into memory for the fixture file, so latency and
    TTFC from a ``--record-fixtures`` run are not valid measurements.
    """
    aclient, auth_aclient = gigachat_http_clients(client)
    api_prev_req, api_prev_resp = _snapshot_hooks(aclient)
    auth_prev_req, auth_prev_resp = _snapshot_hooks(auth_aclient)
    api_state = _RecordingState(
        RecordingConfig(out_dir=config.out_dir, name_prefix=f"{config.name_prefix}_api")
    )
    auth_state = _RecordingState(
        RecordingConfig(out_dir=config.out_dir, name_prefix=f"{config.name_prefix}_auth")
    )
    api_hook = _make_recording_hook(api_state)
    auth_hook = _make_recording_hook(auth_state)
    aclient.event_hooks.setdefault("response", []).append(api_hook)
    auth_aclient.event_hooks.setdefault("response", []).append(auth_hook)

    async def uninstall() -> None:
        _restore_hooks(aclient, request_hooks=api_prev_req, response_hooks=api_prev_resp)
        _restore_hooks(auth_aclient, request_hooks=auth_prev_req, response_hooks=auth_prev_resp)

    return uninstall


@dataclass(slots=True)
class RateLimitCapture:
    """C0 rate-limit metadata observed on the last HTTP 429 response."""

    http_status: int | None = None
    rate_limit_headers: tuple[tuple[str, str], ...] = ()

    def clear(self) -> None:
        """Reset before the next generation call."""
        self.http_status = None
        self.rate_limit_headers = ()

    def observe(self, status: int, headers: httpx.Headers) -> None:
        """Record status and filtered rate-limit headers from a 429 response."""
        if status != _HTTP_TOO_MANY_REQUESTS:
            return
        pairs: list[tuple[str, str]] = []
        for key, value in headers.items():
            lowered = key.lower()
            if lowered == "retry-after" or lowered.startswith("x-ratelimit"):
                pairs.append((lowered, value))
        self.http_status = status
        self.rate_limit_headers = tuple(pairs)


def _make_rate_limit_hook(capture: RateLimitCapture) -> EventHook:
    async def on_response(response: httpx.Response) -> None:
        capture.observe(response.status_code, response.headers)

    return on_response


def install_rate_limit_capture(
    client: GigaChat, capture: RateLimitCapture
) -> Callable[[], Awaitable[None]]:
    """Install 429 header capture via httpx response event hooks. Returns uninstall."""
    aclient, auth_aclient = gigachat_http_clients(client)
    api_prev_req, api_prev_resp = _snapshot_hooks(aclient)
    auth_prev_req, auth_prev_resp = _snapshot_hooks(auth_aclient)
    hook = _make_rate_limit_hook(capture)
    aclient.event_hooks.setdefault("response", []).append(hook)
    auth_aclient.event_hooks.setdefault("response", []).append(hook)

    async def uninstall() -> None:
        _restore_hooks(aclient, request_hooks=api_prev_req, response_hooks=api_prev_resp)
        _restore_hooks(auth_aclient, request_hooks=auth_prev_req, response_hooks=auth_prev_resp)

    return uninstall
