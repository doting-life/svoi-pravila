"""Grafana checks for scripts/prod_smoke.sh (standard library only, TLS verified).

Talks to the Caddy-served Grafana subdomain on 127.0.0.1:<port> with the SNI and Host of the
subdomain, trusting only the Caddy local CA. Covers the Grafana part of the smoke:

  (a) anonymous and header behaviour of the subdomain, snapshots refused;
  (b) admin login, a Viewer created through the admin API, Viewer reads both dashboards and is
      refused when saving;
  (c) the Viewer queries the Postgres datasource: aggregates work, the raw event table, a long
      query and a write are refused; a probe records whether the statement timeout can be lifted
      (information for the report, never a failure);
  (d) six wrong passwords lock the Viewer out (run last: the lockout also covers the client IP).

Every failure exits non-zero with one line on stderr; every pass prints ``prod-smoke: <name>``.
"""

from __future__ import annotations

import argparse
import http.client
import json
import socket
import ssl
import sys
import time
from dataclasses import dataclass
from pathlib import Path

EXPECTED_HSTS = "max-age=31536000; includeSubDomains"
DASHBOARD_TITLES = {"Продуктовая аналитика", "Эксплуатация"}
HOME_REDIRECT = "/d/default-home-dashboard/produktovaja-analitika"
AGGREGATE_TABLE = "analytics_daily_scenario"
RAW_TABLE = "usage_events"
VIEWER_LOGIN = "smoke-viewer"
LOCKOUT_ATTEMPTS = 6
SLEEP_SECONDS = 15
SLEEP_LIMIT_SECONDS = 12
PROBE_SLEEP_SECONDS = 13
REQUEST_TIMEOUT_SECONDS = 40
HTTP_OK = 200
HTTP_OK = 200
POSTGRES_DS = {"uid": "svoi-analytics-pg", "type": "grafana-postgresql-datasource"}


def fail(message: str) -> None:
    print(f"prod-smoke: {message}", file=sys.stderr)
    sys.exit(1)


def ok(name: str) -> None:
    print(f"prod-smoke: {name}")


@dataclass(frozen=True)
class Reply:
    status: int
    headers: dict[str, str]
    body: bytes

    def json(self) -> object:
        return json.loads(self.body)


class Client:
    def __init__(self, host: str, port: int, ca_file: str) -> None:
        self.host = host
        self.ca_file = ca_file
        self.ca_file = ca_file
        self.port = port
        self._context = ssl.create_default_context(cafile=ca_file)
        self.cookies: dict[str, str] = {}

    @property
    def origin(self) -> str:
        return f"https://{self.host}:{self.port}"

    def request(
        self,
        method: str,
        path: str,
        payload: object | None = None,
    ) -> Reply:
        headers = {"Origin": self.origin, "Accept": "application/json"}
        body = None
        if payload is not None:
            body = json.dumps(payload).encode()
            headers["Content-Type"] = "application/json"
        if self.cookies:
            headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in self.cookies.items())
        conn = http.client.HTTPSConnection(
            self.host, self.port, context=self._context, timeout=REQUEST_TIMEOUT_SECONDS
        )
        # The subdomain resolves to loopback; keep its name for SNI and certificate checks.
        raw = socket.create_connection(("127.0.0.1", self.port), timeout=REQUEST_TIMEOUT_SECONDS)
        conn.sock = self._context.wrap_socket(raw, server_hostname=self.host)
        try:
            conn.request(method, path, body=body, headers=headers)
            response = conn.getresponse()
            data = response.read()
            received = {k.lower(): v for k, v in response.getheaders()}
            for name, value in response.getheaders():
                if name.lower() == "set-cookie":
                    pair = value.split(";", 1)[0]
                    key, _, cookie_value = pair.partition("=")
                    self.cookies[key] = cookie_value
            return Reply(response.status, received, data)
        finally:
            conn.close()

    def login(self, user: str, password: str) -> Reply:
        return self.request("POST", "/login", {"user": user, "password": password})


def expect_status(reply: Reply, expected: int | tuple[int, ...], what: str) -> None:
    allowed = (expected,) if isinstance(expected, int) else expected
    if reply.status not in allowed:
        fail(f"{what}: expected {allowed}, got {reply.status}")


def dashboard_uids(directory: Path) -> dict[str, str]:
    uids: dict[str, str] = {}
    for path in sorted(directory.glob("*.json")):
        document = json.loads(path.read_text(encoding="utf-8"))
        uids[document["title"]] = document["uid"]
    return uids


def query_error(reply: Reply) -> str:
    """The error text of a ``/api/ds/query`` answer, or an empty string when it succeeded."""
    parsed = reply.json()
    if not isinstance(parsed, dict):
        fail(f"unexpected /api/ds/query answer: {reply.body[:200]!r}")
    results = parsed.get("results", {})
    frame = results.get("A", {}) if isinstance(results, dict) else {}
    error = frame.get("error", "") if isinstance(frame, dict) else ""
    message = parsed.get("message", "")
    return f"{error} {message}".strip()


def run_sql(client: Client, sql: str) -> tuple[str, float]:
    started = time.monotonic()
    reply = client.request(
        "POST",
        "/api/ds/query",
        {
            "queries": [
                {"refId": "A", "datasource": POSTGRES_DS, "rawSql": sql, "format": "table"}
            ],
            "from": "now-1h",
            "to": "now",
        },
    )
    elapsed = time.monotonic() - started
    if reply.status >= 500 and not reply.body:
        fail(f"/api/ds/query answered {reply.status} without a body")
    return query_error(reply), elapsed


def check_anonymous(client: Client) -> None:
    login = client.request("GET", "/login")
    expect_status(login, 200, "GET /login")
    if login.headers.get("strict-transport-security") != EXPECTED_HSTS:
        fail("grafana HSTS header mismatch")
    csp = login.headers.get("content-security-policy", "")
    if "script-src 'self'" not in csp or "grafana.com" in csp or "wss://" not in csp:
        fail(f"grafana Content-Security-Policy is not the expected one: {csp!r}")
    if login.headers.get("x-frame-options", "").lower() != "deny":
        fail("grafana must forbid framing (X-Frame-Options: deny)")
    if "server" in login.headers:
        fail("the Server header must be removed")
    if login.headers.get("referrer-policy") != "same-origin":
        fail("grafana Referrer-Policy mismatch")
    expect_status(client.request("GET", "/metrics"), 404, "GET /metrics")
    expect_status(client.request("GET", "/api/search"), 401, "anonymous /api/search")
    expect_status(
        client.request("POST", "/api/snapshots", {"dashboard": {"title": "x"}}),
        (401, 403, 404),
        "anonymous snapshot",
    )
    ok("grafana_anonymous_ok")


def check_admin(client: Client, password: str, viewer_password: str) -> None:
    expect_status(client.login("admin", password), 200, "admin login")
    if "grafana_session" not in client.cookies:
        fail("admin login did not set a session cookie")
    expect_status(
        client.request("POST", "/api/snapshots", {"dashboard": {"title": "x"}, "external": False}),
        (403, 404),
        "snapshot creation by admin",
    )
    created = client.request(
        "POST",
        "/api/admin/users",
        {
            "name": "Smoke Viewer",
            "email": "smoke-viewer@example.test",
            "login": VIEWER_LOGIN,
            "password": viewer_password,
        },
    )
    expect_status(created, 200, "creating the Viewer")
    members = client.request("GET", "/api/org/users")
    expect_status(members, 200, "listing organisation users")
    rows = members.json()
    if not isinstance(rows, list):
        fail("unexpected /api/org/users answer")
    roles = {row["login"]: row["role"] for row in rows}
    if roles.get(VIEWER_LOGIN) != "Viewer":
        fail(f"the new user must be a Viewer, got {roles.get(VIEWER_LOGIN)!r}")
    ok("grafana_admin_creates_viewer_ok")


def check_viewer(viewer: Client, password: str, dashboards: dict[str, str]) -> None:
    expect_status(viewer.login(VIEWER_LOGIN, password), 200, "Viewer login")
    for title, uid in dashboards.items():
        reply = viewer.request("GET", f"/api/dashboards/uid/{uid}")
        expect_status(reply, 200, f"Viewer reads dashboard {title}")
    listing = viewer.request("GET", "/api/search?type=dash-db")
    expect_status(listing, 200, "Viewer dashboard list")
    found = listing.json()
    if not isinstance(found, list):
        fail("unexpected /api/search answer")
    titles = {row["title"] for row in found}
    if titles != DASHBOARD_TITLES:
        fail(f"the Viewer must see exactly {sorted(DASHBOARD_TITLES)}, got {sorted(titles)}")
    home = viewer.request("GET", "/api/dashboards/home")
    expect_status(home, 200, "home dashboard")
    home_body = home.json()
    target = home_body.get("redirectUri", "") if isinstance(home_body, dict) else ""
    if target != HOME_REDIRECT:
        fail(f"the home dashboard must be the analytics one ({HOME_REDIRECT}), got {target!r}")
    expect_status(
        viewer.request(
            "POST",
            "/api/dashboards/db",
            {"dashboard": {"title": "viewer-write", "panels": []}, "overwrite": False},
        ),
        403,
        "Viewer saving a dashboard",
    )
    expect_status(
        viewer.request("POST", "/api/snapshots", {"dashboard": {"title": "x"}}),
        (403, 404),
        "snapshot creation by the Viewer",
    )
    ok("grafana_viewer_ok")


def check_datasource(viewer: Client) -> None:
    error, _ = run_sql(viewer, f"SELECT count(*) AS n FROM {AGGREGATE_TABLE}")
    if error:
        fail(f"aggregate SELECT failed: {error}")
    error, _ = run_sql(viewer, f"SELECT count(*) AS n FROM {RAW_TABLE}")
    if "permission denied" not in error:
        fail(f"SELECT on {RAW_TABLE} must be refused with permission denied, got {error!r}")
    error, elapsed = run_sql(viewer, f"SELECT pg_sleep({SLEEP_SECONDS})")
    if "statement timeout" not in error or elapsed >= SLEEP_LIMIT_SECONDS:
        fail(
            f"pg_sleep({SLEEP_SECONDS}) must hit the statement timeout in under "
            f"{SLEEP_LIMIT_SECONDS}s, got {error!r} after {elapsed:.1f}s"
        )
    error, _ = run_sql(viewer, f"INSERT INTO {AGGREGATE_TABLE} DEFAULT VALUES")
    if "read-only" not in error and "permission denied" not in error:
        fail(f"an INSERT must be refused, got {error!r}")
    ok("grafana_datasource_limits_ok")


def check_privilege_boundary(viewer: Client) -> None:
    """The read-only default is user-settable; the privileges of the role are the boundary."""
    error, _ = run_sql(
        viewer,
        f"SET TRANSACTION READ WRITE; INSERT INTO {AGGREGATE_TABLE} DEFAULT VALUES",
    )
    if "permission denied" not in error:
        fail(f"a write with read-only switched off must be denied by privileges, got {error!r}")
    error, _ = run_sql(viewer, "SET ROLE svoi_analytics_read")
    if "permission denied" not in error:
        fail(f"SET ROLE into the group role must be refused, got {error!r}")
    error, _ = run_sql(viewer, f"SET TRANSACTION READ WRITE; SELECT count(*) FROM {RAW_TABLE}")
    if "permission denied" not in error:
        fail(f"the raw event table must stay denied, got {error!r}")
    ok("grafana_privilege_boundary_ok")


def probe_timeout_lift(viewer: Client) -> None:
    """Information only: can a Viewer lift the statement timeout through the datasource?"""
    error, elapsed = run_sql(
        viewer, f"SET statement_timeout = 0; SELECT pg_sleep({PROBE_SLEEP_SECONDS})"
    )
    if error:
        outcome = f"not lifted (error after {elapsed:.1f}s: {error[:120]})"
    else:
        outcome = f"LIFTED (a {PROBE_SLEEP_SECONDS}s query ran for {elapsed:.1f}s)"
    print(f"prod-smoke: info statement_timeout via Grafana: {outcome}")


def check_lockout(viewer_name: str, password: str, client: Client) -> None:
    """Wrong passwords lock the account: even the correct password is refused afterwards."""
    expect_status(client.login(viewer_name, password), 200, "Viewer login before the lockout")
    for _ in range(LOCKOUT_ATTEMPTS):
        client.login(viewer_name, "wrong-password-wrong-password")
    refused = Client(client.host, client.port, client.ca_file).login(viewer_name, password)
    if refused.status == HTTP_OK:
        fail(f"the Viewer can still log in after {LOCKOUT_ATTEMPTS} wrong passwords")
    ok("grafana_lockout_ok")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--ca", required=True)
    parser.add_argument("--admin-password", required=True)
    parser.add_argument("--viewer-password", required=True)
    parser.add_argument("--dashboards", type=Path, required=True)
    args = parser.parse_args()

    dashboards = dashboard_uids(args.dashboards)
    if set(dashboards) != DASHBOARD_TITLES:
        fail(f"unexpected dashboards in {args.dashboards}: {sorted(dashboards)}")

    anonymous = Client(args.host, args.port, args.ca)
    check_anonymous(anonymous)

    admin = Client(args.host, args.port, args.ca)
    check_admin(admin, args.admin_password, args.viewer_password)

    viewer = Client(args.host, args.port, args.ca)
    check_viewer(viewer, args.viewer_password, dashboards)
    check_datasource(viewer)
    check_privilege_boundary(viewer)
    probe_timeout_lift(viewer)
    check_lockout(VIEWER_LOGIN, args.viewer_password, Client(args.host, args.port, args.ca))


if __name__ == "__main__":
    main()
