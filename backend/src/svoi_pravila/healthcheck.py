"""Lightweight container readiness probe (stdlib only).

Must not import the application package graph: each Docker healthcheck
spawns a new process, and a slow import was the stack-smoke flake root cause.
"""

from __future__ import annotations

import http.client
import os
import sys

_HTTP_OK = 200
_TIMEOUT_SECONDS = 2.0
_MAX_TCP_PORT = 65535
_DEFAULT_PORT = 8000


def _port() -> int:
    raw = os.environ.get("SP_HTTP_PORT", str(_DEFAULT_PORT)).strip()
    try:
        port = int(raw)
    except ValueError:
        sys.exit(1)
    if not 1 <= port <= _MAX_TCP_PORT:
        sys.exit(1)
    return port


def main() -> None:
    """GET local ``/readyz``; exit 0 on HTTP 200, otherwise 1."""
    connection = http.client.HTTPConnection(
        "127.0.0.1",
        _port(),
        timeout=_TIMEOUT_SECONDS,
    )
    try:
        connection.request("GET", "/readyz")
        response = connection.getresponse()
        response.read()
        code = response.status
    except (OSError, TimeoutError, http.client.HTTPException):
        sys.exit(1)
    finally:
        connection.close()
    sys.exit(0 if code == _HTTP_OK else 1)


if __name__ == "__main__":
    main()
