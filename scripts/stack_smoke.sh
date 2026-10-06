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

env_get() {
  local key="$1"
  local default="$2"
  local line
  line="$(grep -E "^${key}=" "$ENV_FILE" | head -n1 || true)"
  if [[ -z "$line" ]]; then
    printf '%s' "$default"
    return
  fi
  printf '%s' "${line#*=}"
}

API_PORT="$(env_get API_PORT 18000)"
MINIAPP_PORT="$(env_get MINIAPP_PORT 18080)"
API_BASE="http://127.0.0.1:${API_PORT}"
MINIAPP_BASE="http://127.0.0.1:${MINIAPP_PORT}"

cleanup() {
  make -C "$ROOT" down ENV_FILE="$ENV_FILE" || true
  if [[ "$OWN_TMP" -eq 1 ]]; then
    rm -rf "$CI_TMPDIR"
  fi
}
trap cleanup EXIT

make -C "$ROOT" up ENV_FILE="$ENV_FILE"

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
