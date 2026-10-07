#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ -z "${CI_TMPDIR:-}" ]]; then
  CI_TMPDIR="$(mktemp -d "${TMPDIR:-/tmp}/svoi-pravila-stack-smoke.XXXXXX")"
  OWN_TMP=1
else
  OWN_TMP=0
fi

if [[ -z "${ENV_FILE:-}" ]]; then
  ENV_FILE="${CI_TMPDIR}/env"
fi

ENV_ABS="$(cd "$(dirname "$ENV_FILE")" && pwd)/$(basename "$ENV_FILE")"
case "$ENV_ABS" in
  "$ROOT" | "$ROOT"/*)
    ENV_FILE="${CI_TMPDIR}/env"
    ENV_ABS="$ENV_FILE"
    ;;
esac
export ENV_FILE="$ENV_ABS"

(
  cd "$ROOT/backend"
  uv run --locked python ../scripts/materialize_ci_env.py --env-file "$ENV_FILE" --stack-smoke
)

csp="$(
  cd "$ROOT/backend" && uv run --locked python - <<'PY'
from pathlib import Path
import re
text = Path("../miniapp/Caddyfile").read_text()
match = re.search(r'Content-Security-Policy "([^"]+)"', text)
if match is None:
    raise SystemExit("CSP header missing from miniapp/Caddyfile")
print(match.group(1))
PY
)"
export MINIAPP_CSP="$csp"

compose_port() {
  local service="$1"
  local container_port="$2"
  docker compose -p "${COMPOSE_PROJECT_NAME:-svoi-pravila-ci}" --env-file "$ENV_FILE" \
    port "$service" "$container_port"
}

cleanup() {
  make -C "$ROOT" observability-down ENV_FILE="$ENV_FILE" || true
  make -C "$ROOT" down ENV_FILE="$ENV_FILE" || true
  if [[ "$OWN_TMP" -eq 1 ]]; then
    rm -rf "$CI_TMPDIR"
  fi
}
trap cleanup EXIT

make -C "$ROOT" up ENV_FILE="$ENV_FILE"
make -C "$ROOT" observability-up ENV_FILE="$ENV_FILE"

API_BIND="$(compose_port api 8000)"
MINIAPP_BIND="$(compose_port miniapp 8080)"
GRAFANA_BIND="$(compose_port grafana 3000)"
API_BASE="http://${API_BIND}"
MINIAPP_BASE="http://${MINIAPP_BIND}"
GRAFANA_BASE="http://${GRAFANA_BIND}"
echo "stack-smoke: api=${API_BASE} miniapp=${MINIAPP_BASE} grafana=${GRAFANA_BASE}"

code="$(curl -s -o "${CI_TMPDIR}/readyz.json" -w '%{http_code}' "${API_BASE}/readyz")"
test "$code" = "200"

headers="${CI_TMPDIR}/headers"
code="$(curl -sD "$headers" -o "${CI_TMPDIR}/miniapp-index.html" -w '%{http_code}' "${MINIAPP_BASE}/")"
test "$code" = "200"
csp_got="$(awk -F': ' 'tolower($1)=="content-security-policy"{sub(/\r$/,"",$2); print $2; exit}' "$headers")"
test "$csp_got" = "$MINIAPP_CSP"
asset_path="$(grep -oE '/assets/[^"]+' "${CI_TMPDIR}/miniapp-index.html" | head -n1)"
test -n "$asset_path"
asset_code="$(curl -s -o /dev/null -w '%{http_code}' "${MINIAPP_BASE}${asset_path}")"
test "$asset_code" = "200"
health_code="$(curl -s -o /dev/null -w '%{http_code}' "${MINIAPP_BASE}/healthz")"
test "$health_code" = "404"

code="$(dd if=/dev/zero bs=1024 count=20 2>/dev/null \
  | curl -s -o "${CI_TMPDIR}/body-limit.json" -w '%{http_code}' \
    -X POST "${MINIAPP_BASE}/api/v1/contacts" \
    -H 'Content-Type: application/json' \
    --data-binary @-)"
test "$code" = "413"

(
  cd "$ROOT/backend" && uv run --locked python - <<PY
import time
import urllib.request

url = "${MINIAPP_BASE}/api/v1/_test/sse-flush"
started = time.monotonic()
with urllib.request.urlopen(url, timeout=10) as resp:
    assert resp.headers.get_content_type() == "text/event-stream"
    first = resp.read(16)
    first_at = time.monotonic() - started
assert b"event:" in first or b"data:" in first, first
assert first_at < 1.5, f"first SSE bytes arrived too late: {first_at:.3f}s"
print(f"sse_flush_ok first_bytes_at={first_at:.3f}s")
PY
)

(
  cd "$ROOT/backend" && uv run --locked python - <<PY
import base64
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

env_path = Path(os.environ["ENV_FILE"])
vals: dict[str, str] = {}
for line in env_path.read_text().splitlines():
    if not line or line.startswith("#") or "=" not in line:
        continue
    key, value = line.split("=", 1)
    vals[key] = value
admin_password = vals["SP_GRAFANA_ADMIN_PASSWORD"]
token = base64.b64encode(f"admin:{admin_password}".encode()).decode()
auth = {"Authorization": f"Basic {token}"}
base = "${GRAFANA_BASE}"

with urllib.request.urlopen(urllib.request.Request(f"{base}/api/health"), timeout=10) as resp:
    health = json.loads(resp.read().decode())
assert health.get("database") == "ok", health
print("grafana_health_ok")

deadline = time.monotonic() + 90
ds_health = None
last_err = None
while time.monotonic() < deadline:
    try:
        ds_req = urllib.request.Request(
            f"{base}/api/datasources/uid/svoi-analytics-pg/health",
            headers=auth,
        )
        with urllib.request.urlopen(ds_req, timeout=30) as resp:
            ds_health = json.loads(resp.read().decode())
        if ds_health.get("status") == "OK":
            break
    except urllib.error.HTTPError as exc:
        last_err = exc
        body = exc.read().decode() if exc.fp is not None else ""
        if exc.code not in {404, 502, 503}:
            raise
        time.sleep(2)
        continue
    time.sleep(2)
else:
    list_req = urllib.request.Request(f"{base}/api/datasources", headers=auth)
    with urllib.request.urlopen(list_req, timeout=10) as resp:
        listing = resp.read().decode()
    raise AssertionError(
        f"datasource health not OK: last={ds_health!r} err={last_err!r} listing={listing}"
    )
print("grafana_datasource_ok")

dash_req = urllib.request.Request(
    f"{base}/api/dashboards/uid/svoi-analytics",
    headers=auth,
)
with urllib.request.urlopen(dash_req, timeout=10) as resp:
    dash = json.loads(resp.read().decode())
assert dash["dashboard"]["uid"] == "svoi-analytics", dash
print("grafana_dashboard_ok")

deadline = time.monotonic() + 90
metrics_health = None
while time.monotonic() < deadline:
    try:
        ds_req = urllib.request.Request(
            f"{base}/api/datasources/uid/svoi-metrics/health",
            headers=auth,
        )
        with urllib.request.urlopen(ds_req, timeout=30) as resp:
            metrics_health = json.loads(resp.read().decode())
        if metrics_health.get("status") == "OK":
            break
    except urllib.error.HTTPError as exc:
        if exc.code not in {404, 502, 503}:
            raise
        time.sleep(2)
        continue
    time.sleep(2)
else:
    raise AssertionError(f"metrics datasource health not OK: {metrics_health!r}")
print("grafana_metrics_datasource_ok")

ops_req = urllib.request.Request(
    f"{base}/api/dashboards/uid/svoi-ops",
    headers=auth,
)
with urllib.request.urlopen(ops_req, timeout=10) as resp:
    ops = json.loads(resp.read().decode())
assert ops["dashboard"]["uid"] == "svoi-ops", ops
print("grafana_ops_dashboard_ok")
PY
)

# C3 — Prometheus scrapes api:9100; host and Grafana cannot reach the metrics port.
project="${COMPOSE_PROJECT_NAME:-svoi-pravila-ci}"
compose=(docker compose -p "$project" --env-file "$ENV_FILE")
prom_deadline=$((SECONDS + 90))
prom_up=""
while (( SECONDS < prom_deadline )); do
  prom_up="$("${compose[@]}" exec -T prometheus wget -qO- \
    'http://127.0.0.1:9090/api/v1/query?query=up%7Bjob%3D%22api%22%7D' 2>/dev/null || true)"
  if printf '%s' "$prom_up" | grep -q '"value":\[.*,"1"\]'; then
    break
  fi
  sleep 2
done
printf '%s\n' "$prom_up" | grep -q '"value":\[.*,"1"\]'
echo "prometheus_api_up_ok"

if curl -sS -m 2 "http://127.0.0.1:9100/metrics" >/dev/null 2>&1; then
  echo "metrics port unexpectedly reachable on host" >&2
  exit 1
fi
echo "host_metrics_port_closed_ok"


# C2 — Grafana must not receive app secrets (names only; never print values).
forbidden_env="$(
  docker compose -p "${COMPOSE_PROJECT_NAME:-svoi-pravila-ci}" --env-file "$ENV_FILE" \
    exec -T grafana sh -c 'env | cut -d= -f1' | sort -u
)"
for name in \
  SP_TELEGRAM_BOT_TOKEN \
  SP_TELEGRAM_UPDATES_MODE \
  SP_GIGACHAT_CREDENTIALS \
  SP_GIGACHAT_SCOPE \
  SP_DATA_KEK \
  SP_PSEUDONYM_PEPPER \
  SP_DATABASE_URL
do
  if printf '%s\n' "$forbidden_env" | grep -qx "$name"; then
    echo "grafana env leak: ${name}" >&2
    exit 1
  fi
done
# Pattern families from the task (prefix match on names only).
if printf '%s\n' "$forbidden_env" | grep -E '^(SP_TELEGRAM_|SP_GIGACHAT_|SP_DATA_KEK)'; then
  echo "grafana env leak: forbidden prefix present" >&2
  exit 1
fi
echo "grafana_env_names_ok"

# B2 — From inside Grafana: Postgres reachable; api and valkey isolated.
obs_net="$(docker network ls --format '{{.Name}}' | grep -E "^${project}_observability$" | head -n1)"
edge_net="$(docker network ls --format '{{.Name}}' | grep -E "^${project}_grafana_edge$" | head -n1)"
metrics_net="$(docker network ls --format '{{.Name}}' | grep -E "^${project}_metrics$" | head -n1)"
test -n "$obs_net"
test -n "$edge_net"
test -n "$metrics_net"
internal="$(docker network inspect "$obs_net" --format '{{.Internal}}')"
test "$internal" = "true"
edge_internal="$(docker network inspect "$edge_net" --format '{{.Internal}}')"
test "$edge_internal" = "false"
metrics_internal="$(docker network inspect "$metrics_net" --format '{{.Internal}}')"
test "$metrics_internal" = "true"

"${compose[@]}" exec -T grafana sh -c '
set -eu
# BusyBox wget: TCP open then protocol failure still proves the port is reachable.
# "bad address" / resolve failure means the hostname is not on Grafana networks.
probe_ok() {
  host="$1"
  port="$2"
  err="$(wget -T 2 -O /dev/null "http://${host}:${port}/" 2>&1 || true)"
  printf "%s\n" "$err" | grep -qiE "bad address|Name or service not known|nodename nor servname" && return 1
  printf "%s\n" "$err" | grep -qiE "connection refused" && return 1
  return 0
}
probe_fail() {
  host="$1"
  port="$2"
  err="$(wget -T 2 -O /dev/null "http://${host}:${port}/" 2>&1 || true)"
  printf "%s\n" "$err" | grep -qiE "bad address|Name or service not known|nodename nor servname|connection refused|timed out|Network is unreachable" && return 0
  return 1
}
probe_ok postgres 5432
probe_ok prometheus 9090
probe_fail api 8000
probe_fail api 9100
probe_fail valkey 6379
'
echo "grafana_isolation_ok"
