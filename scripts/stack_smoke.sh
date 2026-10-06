#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ -z "${ENV_FILE:-}" ]]; then
  ENV_FILE="$(mktemp "${TMPDIR:-/tmp}/svoi-pravila-stack-smoke.XXXXXX")"
  export ENV_FILE
fi

ENV_ABS="$(cd "$(dirname "$ENV_FILE")" && pwd)/$(basename "$ENV_FILE")"
REPO_ABS="$(pwd)"
case "$ENV_ABS" in
  "$REPO_ABS" | "$REPO_ABS"/*)
    ENV_FILE="$(mktemp "${TMPDIR:-/tmp}/svoi-pravila-stack-smoke.XXXXXX")"
    ENV_ABS="$(cd "$(dirname "$ENV_FILE")" && pwd)/$(basename "$ENV_FILE")"
    ;;
esac

export ENV_FILE="$ENV_ABS"

cd "$ROOT/backend" && uv run python ../scripts/materialize_stack_smoke_env.py --env-file "$ENV_FILE"

csp="$(cd "$ROOT/backend" && uv run python - <<'PY'
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

cleanup() {
  make down ENV_FILE="$ENV_FILE" || true
}
trap cleanup EXIT

make up ENV_FILE="$ENV_FILE"

code="$(curl -s -o /tmp/svoi-pravila-readyz.json -w '%{http_code}' http://127.0.0.1:8000/readyz)"
test "$code" = "200"

headers="$(mktemp "${TMPDIR:-/tmp}/svoi-pravila-headers.XXXXXX")"
code="$(curl -sD "$headers" -o /tmp/svoi-pravila-miniapp-index.html -w '%{http_code}' http://127.0.0.1:8080/)"
test "$code" = "200"
csp_got="$(awk -F': ' 'tolower($1)=="content-security-policy"{sub(/\r$/,"",$2); print $2; exit}' "$headers")"
test "$csp_got" = "$MINIAPP_CSP"
asset_path="$(grep -oE '/assets/[^"]+' /tmp/svoi-pravila-miniapp-index.html | head -n1)"
test -n "$asset_path"
asset_code="$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:8080${asset_path}")"
test "$asset_code" = "200"
health_code="$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8080/healthz)"
test "$health_code" = "404"

code="$(dd if=/dev/zero bs=1024 count=20 2>/dev/null \
  | curl -s -o /tmp/svoi-pravila-body-limit.json -w '%{http_code}' \
    -X POST http://127.0.0.1:8080/api/v1/contacts \
    -H 'Content-Type: application/json' \
    --data-binary @-)"
test "$code" = "413"

cd "$ROOT/backend" && uv run python - <<'PY'
import time
import urllib.request

url = "http://127.0.0.1:8080/api/v1/_test/sse-flush"
started = time.monotonic()
with urllib.request.urlopen(url, timeout=10) as resp:
    assert resp.headers.get_content_type() == "text/event-stream"
    first = resp.read(16)
    first_at = time.monotonic() - started
assert b"event:" in first or b"data:" in first, first
assert first_at < 1.5, f"first SSE bytes arrived too late: {first_at:.3f}s"
print(f"sse_flush_ok first_bytes_at={first_at:.3f}s")
PY
