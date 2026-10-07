"""Start the internal Prometheus scrape HTTP server."""

from __future__ import annotations

from prometheus_client import start_http_server

# All interfaces inside the container (metrics network only; not published).
_CONTAINER_BIND = f"{0}.{0}.{0}.{0}"


def start_metrics_server(*, port: int, addr: str | None = None) -> None:
    """Bind ``prometheus_client``'s scrape server inside the process.

    Default process and platform collectors remain on the default registry.
    """
    start_http_server(port, addr=_CONTAINER_BIND if addr is None else addr)
