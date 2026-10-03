"""Unit tests for the container healthcheck console script."""

from __future__ import annotations

import socket
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, ClassVar, cast

import pytest

from svoi_pravila.bootstrap import healthcheck
from tests.factories import make_settings


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _handler(
    *,
    status_code: int,
    body: bytes = b"",
    block_event: threading.Event | None = None,
) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        configured_status: ClassVar[int] = status_code
        configured_body: ClassVar[bytes] = body
        configured_block: ClassVar[threading.Event | None] = block_event

        def log_message(self, *_args: object) -> None:
            return

    def handle(self: Handler) -> None:
        if self.configured_block is not None:
            self.configured_block.wait()
        self.send_response(self.configured_status)
        self.end_headers()
        if self.configured_body:
            self.wfile.write(self.configured_body)

    cast(Any, Handler).do_GET = handle
    return Handler


@contextmanager
def _local_http_server(
    handler: type[BaseHTTPRequestHandler],
    *,
    release_on_teardown: threading.Event | None = None,
) -> Iterator[int]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield int(server.server_address[1])
    finally:
        if release_on_teardown is not None:
            release_on_teardown.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)


@pytest.mark.unit
def test_healthcheck_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    with _local_http_server(_handler(status_code=200, body=b'{"ready":true}')) as port:
        monkeypatch.setattr(
            "svoi_pravila.bootstrap.load_settings",
            lambda: make_settings(http_port=port),
        )
        with pytest.raises(SystemExit) as exited:
            healthcheck()
        assert exited.value.code == 0


@pytest.mark.unit
def test_healthcheck_not_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    with _local_http_server(_handler(status_code=503, body=b'{"ready":false}')) as port:
        monkeypatch.setattr(
            "svoi_pravila.bootstrap.load_settings",
            lambda: make_settings(http_port=port),
        )
        with pytest.raises(SystemExit) as exited:
            healthcheck()
        assert exited.value.code == 1


@pytest.mark.unit
def test_healthcheck_connection_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    port = _free_port()
    monkeypatch.setattr(
        "svoi_pravila.bootstrap.load_settings",
        lambda: make_settings(http_port=port),
    )
    with pytest.raises(SystemExit) as exited:
        healthcheck()
    assert exited.value.code == 1


@pytest.mark.unit
def test_healthcheck_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    block = threading.Event()
    with _local_http_server(
        _handler(status_code=200, block_event=block),
        release_on_teardown=block,
    ) as port:
        monkeypatch.setattr(
            "svoi_pravila.bootstrap.load_settings",
            lambda: make_settings(http_port=port),
        )
        with pytest.raises(SystemExit) as exited:
            healthcheck()
        assert exited.value.code == 1
