"""Start the internal Prometheus scrape HTTP server."""

from __future__ import annotations

from prometheus_client import start_http_server


def start_metrics_server(*, host: str, port: int) -> None:
    """Bind ``prometheus_client``'s scrape server inside the process.

    Default process and platform collectors remain on the default registry.
    ``host`` comes from settings (loopback by default; compose uses ``0.0.0.0``).
    """
    start_http_server(port, addr=host)
