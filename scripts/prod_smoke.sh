#!/usr/bin/env bash
# Production-shaped smoke: compose.prod.yaml + compose.prod-smoke.yaml with locally built images,
# a Caddy-issued local certificate, an offline Telegram stub and local-first backups
# (with and without optional rclone offsite).
# Expects `make image` to have produced the svoi-pravila*:ci images. Never reads the repo .env.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# shellcheck source=scripts/image-pins.env
source "$ROOT/scripts/image-pins.env"

PROJECT="${PROD_SMOKE_PROJECT:-svoi-pravila-prod-smoke}"
DOMAIN="svoi.localhost"
EXPECTED_HSTS="max-age=31536000; includeSubDomains"
RELEASE_SHA="0000000000000000000000000000000000000000"
OFFSITE_WARN="backup: offsite not configured (local copy only)"
BACKUP_UID=1500
BACKUP_MODE=600

if [[ -z "${CI_TMPDIR:-}" ]]; then
  TMP="$(mktemp -d "${TMPDIR:-/tmp}/svoi-pravila-prod-smoke.XXXXXX")"
else
  TMP="$(mktemp -d "${CI_TMPDIR}/prod-smoke.XXXXXX")"
fi
ENV_FILE="${TMP}/env"
RELEASE_ENV="${TMP}/release.env"
STUB_DIR="${TMP}/stub"
BACKUPS_DIR="${TMP}/backups"
BACKUP_REMOTE_DIR="${TMP}/backup-remote"
# Offsite target must live under the /backups bind so rclone inside the container can see it.
OFFSITE_DIR="${BACKUPS_DIR}/.offsite-sp"
mkdir -p "$STUB_DIR" "$BACKUPS_DIR" "$BACKUP_REMOTE_DIR" "$OFFSITE_DIR"
# Container runs as UID 1500; make the bind writable without requiring host root chown.
chmod 0777 "$BACKUPS_DIR"
export SP_ENV_FILE="$ENV_FILE"

compose=(
  docker compose -p "$PROJECT"
  --project-directory "$ROOT/deploy"
  -f "$ROOT/deploy/compose.prod.yaml"
  -f "$ROOT/deploy/compose.prod-smoke.yaml"
  --env-file "$ENV_FILE"
  --env-file "$RELEASE_ENV"
)

fail() {
  echo "prod-smoke: $*" >&2
  exit 1
}

# UID 1500 may own trees under the backups bind on Linux; remove via the image as root.
wipe_backups_bind() {
  if [[ ! -d "$BACKUPS_DIR" ]]; then
    return 0
  fi
  if [[ -z "${BACKUP_IMAGE:-}" ]]; then
    rm -rf "${BACKUPS_DIR:?}/"*
    return 0
  fi
  docker run --rm --user 0:0 \
    -v "${BACKUPS_DIR}:/backups" \
    --entrypoint sh "$BACKUP_IMAGE" \
    -c 'find /backups -mindepth 1 -maxdepth 1 -exec rm -rf {} +'
}

cleanup() {
  local rc=$?
  if [[ "$rc" -ne 0 && -f "$RELEASE_ENV" ]]; then
    echo "prod-smoke: FAILED (exit ${rc}); recent service logs follow" >&2
    # Keep the tail short so the quiet CI summary still shows the fail reason above.
    "${compose[@]}" logs --no-color --tail 20 api migrate miniapp telegram-stub >&2 || true
  fi
  if [[ -f "$RELEASE_ENV" ]]; then
    # Best-effort teardown after the smoke run.
    "${compose[@]}" --profile backup down -v --remove-orphans >/dev/null 2>&1 || true
  fi
  # Best-effort: Linux bind trees owned by UID 1500 must not mask the original exit.
  wipe_backups_bind >/dev/null 2>&1 || true
  rm -rf "$TMP" || true
  exit "$rc"
}
trap cleanup EXIT

image_id() {
  docker image inspect --format '{{.Id}}' "$1" 2>/dev/null \
    || fail "$1 is missing; run make image first"
}

API_IMAGE="$(image_id svoi-pravila:ci)"
MINIAPP_IMAGE="$(image_id svoi-pravila-miniapp:ci)"
GRAFANA_IMAGE="$(image_id svoi-pravila-grafana:ci)"
BACKUP_IMAGE="$(image_id svoi-pravila-backup:ci)"

set_key() {
  local file="$1" key="$2" value="$3"
  awk -v k="$key" -v v="$value" '
    BEGIN { FS = "=" }
    $1 == k { print k "=" v; found = 1; next }
    { print }
    END { if (!found) exit 3 }
  ' "$file" >"${file}.tmp"
  mv "${file}.tmp" "$file"
}

env_value() {
  grep -E "^$1=" "$ENV_FILE" | head -n 1 | cut -d= -f2-
}

free_port() {
  (cd "$ROOT/backend" && uv run --locked python -c '
import socket
with socket.socket() as sock:
    sock.bind(("127.0.0.1", 0))
    print(sock.getsockname()[1])
')
}

HTTP_PORT="$(free_port)"
HTTPS_PORT="$(free_port)"

echo "prod-smoke: generating stub certificates and backup key"
cert_out="$(docker run --rm --user "$(id -u):$(id -g)" \
  -v "${STUB_DIR}:/out" -v "${ROOT}/scripts/prod_smoke/gen_stub_certs.py:/gen.py:ro" \
  --entrypoint python "$API_IMAGE" /gen.py /out)"
CERTIFI_BUNDLE="$(printf '%s\n' "$cert_out" | tail -n 1)"
cp "$ROOT/scripts/prod_smoke/telegram_stub.py" "$STUB_DIR/telegram_stub.py"
chmod 0644 "$STUB_DIR"/*
chmod 0755 "$STUB_DIR"

keygen_out="$(docker run --rm --user "$(id -u):$(id -g)" -v "${TMP}:/out" \
  --entrypoint age-keygen "$BACKUP_IMAGE" -o /out/age.key 2>&1)"
AGE_RECIPIENT="$(printf '%s\n' "$keygen_out" | grep -oE 'age1[02-9ac-hj-np-z]{58}' | head -n 1)"
[[ -n "$AGE_RECIPIENT" ]] || fail "could not read the age public key"
chmod 0644 "${TMP}/age.key"

cp "$ROOT/deploy/env.prod.example" "$ENV_FILE"
chmod 0600 "$ENV_FILE"
set_key "$ENV_FILE" SP_DOMAIN "$DOMAIN"
set_key "$ENV_FILE" SP_ACME_EMAIL "prod-smoke@example.test"
set_key "$ENV_FILE" SP_DATA_KEK "$(openssl rand -base64 32)"
set_key "$ENV_FILE" SP_PSEUDONYM_PEPPER "$(openssl rand -base64 32)"
set_key "$ENV_FILE" SP_GIGACHAT_CREDENTIALS "prod-smoke-placeholder"
set_key "$ENV_FILE" SP_TELEGRAM_BOT_TOKEN "123456:PROD-SMOKE-STUB"
set_key "$ENV_FILE" SP_TELEGRAM_WEBHOOK_PATH_SECRET "$(openssl rand -hex 24)"
set_key "$ENV_FILE" SP_TELEGRAM_WEBHOOK_SECRET_TOKEN "$(openssl rand -hex 24)"
set_key "$ENV_FILE" SP_LLM_DAILY_TOKEN_BUDGET "20000"
set_key "$ENV_FILE" POSTGRES_PASSWORD "$(openssl rand -hex 24)"
set_key "$ENV_FILE" VALKEY_PASSWORD "$(openssl rand -hex 24)"
set_key "$ENV_FILE" SP_GRAFANA_DB_PASSWORD "$(openssl rand -hex 24)"
set_key "$ENV_FILE" SP_GRAFANA_ADMIN_PASSWORD "$(openssl rand -hex 24)"
set_key "$ENV_FILE" SP_GHCR_OWNER "ci"
set_key "$ENV_FILE" SP_GHCR_USER "ci"
set_key "$ENV_FILE" SP_BACKUP_AGE_RECIPIENT "$AGE_RECIPIENT"
set_key "$ENV_FILE" SP_BACKUP_REMOTE_PATH "svoi-pravila"
if grep -qE '^[A-Z][A-Z0-9_]*=CHANGE_ME$' "$ENV_FILE"; then
  fail "deploy/env.prod.example has CHANGE_ME values the smoke test does not fill"
fi

cat >"$RELEASE_ENV" <<EOF
API_IMAGE=${API_IMAGE}
MINIAPP_IMAGE=${MINIAPP_IMAGE}
GRAFANA_IMAGE=${GRAFANA_IMAGE}
BACKUP_IMAGE=${BACKUP_IMAGE}
PROMETHEUS_IMAGE=${PROMETHEUS_IMAGE}
SMOKE_HTTP_PORT=${HTTP_PORT}
SMOKE_HTTPS_PORT=${HTTPS_PORT}
SMOKE_STUB_DIR=${STUB_DIR}
SMOKE_CERTIFI_BUNDLE=${CERTIFI_BUNDLE}
SMOKE_BACKUPS_DIR=${BACKUPS_DIR}
SMOKE_BACKUP_REMOTE_DIR=${BACKUP_REMOTE_DIR}
EOF

PATH_SECRET="$(env_value SP_TELEGRAM_WEBHOOK_PATH_SECRET)"
SECRET_TOKEN="$(env_value SP_TELEGRAM_WEBHOOK_SECRET_TOKEN)"

"${compose[@]}" --profile backup down -v --remove-orphans >/dev/null 2>&1 || true
"${compose[@]}" config --quiet
echo "prod-smoke: starting the production stack"
"${compose[@]}" up -d --wait --wait-timeout 300

"${compose[@]}" cp miniapp:/data/caddy/pki/authorities/local/root.crt "${TMP}/caddy-root.crt"

https_curl() {
  curl -sS --max-time 10 --cacert "${TMP}/caddy-root.crt" \
    --resolve "${DOMAIN}:${HTTPS_PORT}:127.0.0.1" "$@"
}
https_url() {
  printf 'https://%s:%s%s' "$DOMAIN" "$HTTPS_PORT" "$1"
}

headers="${TMP}/headers"
code=""
for _ in $(seq 1 30); do
  code="$(https_curl -D "$headers" -o "${TMP}/index.html" -w '%{http_code}' "$(https_url /)" || true)"
  [[ "$code" == "200" ]] && break
  sleep 2
done
[[ "$code" == "200" ]] || fail "HTTPS / returned '${code}'"

header_value() {
  awk -F': ' -v name="$1" 'tolower($1) == name { sub(/\r$/, "", $2); print $2; exit }' "$headers"
}
csp_of() {
  sed -n 's/.*Content-Security-Policy "\([^"]*\)".*/\1/p' "$1" | head -n 1
}
expected_csp="$(csp_of "$ROOT/miniapp/Caddyfile.prod")"
[[ -n "$expected_csp" ]] || fail "CSP missing from miniapp/Caddyfile.prod"
[[ "$expected_csp" == "$(csp_of "$ROOT/miniapp/Caddyfile")" ]] \
  || fail "CSP in Caddyfile.prod differs from Caddyfile"
[[ "$(header_value content-security-policy)" == "$expected_csp" ]] || fail "CSP header mismatch"
[[ "$(header_value strict-transport-security)" == "$EXPECTED_HSTS" ]] || fail "HSTS header mismatch"
echo "prod-smoke: https_csp_hsts_ok"

redirect="$(curl -sS --max-time 10 -o /dev/null -w '%{http_code} %{redirect_url}' \
  --resolve "${DOMAIN}:${HTTP_PORT}:127.0.0.1" "http://${DOMAIN}:${HTTP_PORT}/x?y=1")"
[[ "$redirect" == "301 https://${DOMAIN}/x?y=1" ]] || fail "http redirect was '${redirect}'"
echo "prod-smoke: http_redirect_ok"

code="$(https_curl -o /dev/null -w '%{http_code}' "$(https_url /healthz)")"
[[ "$code" == "404" ]] || fail "/healthz returned ${code}"

webhook_status() {
  https_curl -o /dev/null -w '%{http_code}' -X POST "$(https_url "$1")" \
    -H 'Content-Type: application/json' \
    -H "X-Telegram-Bot-Api-Secret-Token: $2" \
    --data '{"update_id":1}'
}
code="$(webhook_status "/tg/${PATH_SECRET}" "$SECRET_TOKEN")"
[[ "$code" == "200" ]] || fail "webhook with good token returned ${code}"
code="$(webhook_status "/tg/${PATH_SECRET}" "wrong-token-wrong-token-wrong-token-1")"
[[ "$code" == "404" ]] || fail "webhook with bad token returned ${code}"
code="$(webhook_status "/tg/${PATH_SECRET}x" "$SECRET_TOKEN")"
[[ "$code" != "200" ]] || fail "webhook with a wrong path secret reached the API"
code="$(webhook_status "/telegram/webhook/${PATH_SECRET}" "$SECRET_TOKEN")"
[[ "$code" != "200" ]] || fail "internal webhook path is reachable from outside"
echo "prod-smoke: webhook_auth_ok"

registered="$("${compose[@]}" exec -T telegram-stub python -c '
import json
import ssl
import urllib.request

context = ssl.create_default_context(cafile="/stub/ca.crt")
request = urllib.request.Request("https://api.telegram.org/bot1:x/getWebhookInfo", method="POST")
with urllib.request.urlopen(request, context=context, timeout=5) as response:
    print(json.load(response)["result"]["url"])
')"
[[ "$registered" == "https://${DOMAIN}/tg/${PATH_SECRET}" ]] \
  || fail "registered webhook URL mismatch: ${registered//${PATH_SECRET}/<secret>}"
echo "prod-smoke: webhook_registered_ok"

code="$(https_curl -o /dev/null -w '%{http_code}' "$(https_url /api/v1/me)")"
[[ "$code" == "401" ]] || fail "/api/v1/me without credentials returned ${code}"
echo "prod-smoke: api_proxy_ok"

published_ports() {
  local container
  container="$("${compose[@]}" ps -aq "$1")"
  [[ -n "$container" ]] || fail "service $1 has no container"
  docker port "$container" 2>/dev/null || true
}
for service in postgres valkey migrate api prometheus; do
  ports="$(published_ports "$service")"
  [[ -z "$ports" ]] || fail "${service} publishes host ports: ${ports}"
done
grafana_ports="$(published_ports grafana)"
[[ -n "$grafana_ports" ]] || fail "grafana publishes no port"
if printf '%s\n' "$grafana_ports" | grep -qv -- '-> 127.0.0.1:'; then
  fail "grafana is not bound to loopback only: ${grafana_ports}"
fi
for net in data metrics observability; do
  [[ "$(docker network inspect --format '{{.Internal}}' "${PROJECT}_${net}")" == "true" ]] \
    || fail "network ${net} must be internal"
done
[[ "$(docker network inspect --format '{{.Internal}}' "${PROJECT}_edge")" == "false" ]] \
  || fail "network edge must not be internal"
echo "prod-smoke: isolation_ok"

prepare_backup_dirs() {
  # Host-owned 0777 dirs: UID 1500 can write dumps (mode 600); the runner can wipe the bind.
  mkdir -p "$BACKUPS_DIR/daily" \
    "$BACKUPS_DIR/monthly" \
    "$BACKUPS_DIR/pre-release" \
    "${OFFSITE_DIR}/svoi-pravila/daily" \
    "${OFFSITE_DIR}/svoi-pravila/monthly" \
    "${OFFSITE_DIR}/svoi-pravila/pre-release"
  chmod 0777 "$BACKUPS_DIR" \
    "$BACKUPS_DIR/daily" \
    "$BACKUPS_DIR/monthly" \
    "$BACKUPS_DIR/pre-release" \
    "$OFFSITE_DIR" \
    "${OFFSITE_DIR}/svoi-pravila" \
    "${OFFSITE_DIR}/svoi-pravila/daily" \
    "${OFFSITE_DIR}/svoi-pravila/monthly" \
    "${OFFSITE_DIR}/svoi-pravila/pre-release"
}

seed_named_dumps() {
  local target_dir="$1" count="$2" prefix="$3"
  local i stamp
  mkdir -p "$target_dir"
  # World-writable so UID 1500 can prune host-created seeds (Linux bind mounts).
  chmod 0777 "$target_dir"
  for ((i = 1; i <= count; i++)); do
    stamp="$(printf '%s%02dT000000Z' "$prefix" "$i")"
    # Encrypted garbage is enough for retention prune; restore-verify uses real dumps.
    printf 'seed-%s' "$stamp" | docker run --rm -i --entrypoint age "$BACKUP_IMAGE" \
      --recipient "$AGE_RECIPIENT" >"${target_dir}/svoi-pravila-${stamp}.dump.age"
    chmod 0666 "${target_dir}/svoi-pravila-${stamp}.dump.age"
  done
}

decrypt_dump_ok() {
  local path="$1"
  [[ -s "$path" ]] || fail "missing encrypted dump ${path}"
  docker run --rm \
    -v "${TMP}/age.key:/run/age.key:ro" \
    -v "${path}:/in.dump.age:ro" \
    --entrypoint age "$BACKUP_IMAGE" \
    --decrypt --identity /run/age.key /in.dump.age >/dev/null \
    || fail "age decrypt failed for ${path}"
}

dump_basenames() {
  local dir="$1"
  local -a files=()
  local f
  shopt -s nullglob
  files=("${dir}"/svoi-pravila-*.dump.age)
  shopt -u nullglob
  for f in "${files[@]}"; do
    basename "$f"
  done | sort
}

count_age() {
  local dir="$1"
  local -a files=()
  shopt -s nullglob
  files=("${dir}"/svoi-pravila-*.dump.age)
  shopt -u nullglob
  printf '%s\n' "${#files[@]}"
}

newest_dump_basename() {
  local dir="$1"
  dump_basenames "$dir" | tail -n 1
}

first_dump_basename() {
  local dir="$1"
  dump_basenames "$dir" | head -n 1
}

assert_dump_owner_mode() {
  # Assert inside the container: Docker Desktop remaps UIDs on the host bind.
  local rel="$1"
  local got
  got="$("${compose[@]}" run --rm -T --no-deps --entrypoint stat backup \
    -c '%u %a' "/backups/${rel}")"
  got="$(printf '%s' "$got" | tr -d '\r')"
  [[ "$got" == "${BACKUP_UID} ${BACKUP_MODE}" ]] \
    || fail "expected uid=${BACKUP_UID} mode=${BACKUP_MODE} for /backups/${rel}, got '${got}'"
  echo "prod-smoke: dump_owner_mode_ok uid=${BACKUP_UID} mode=${BACKUP_MODE} path=/backups/${rel}"
}

echo "prod-smoke: backups segment 1 (local only, no rclone.conf)"
wipe_backups_bind
prepare_backup_dirs
rm -f "${BACKUP_REMOTE_DIR}/rclone.conf"
backup_log="${TMP}/backup-local.log"
if ! "${compose[@]}" run --rm -T backup >"$backup_log" 2>&1; then
  cat "$backup_log" >&2
  fail "daily backup (local-only) failed"
fi
grep -F "$OFFSITE_WARN" "$backup_log" >/dev/null \
  || fail "expected warning '${OFFSITE_WARN}' in local-only backup log"
"${compose[@]}" run --rm -T -e "SP_RELEASE_SHA=${RELEASE_SHA}" backup --pre-release \
  >"${TMP}/backup-prerelease.log" 2>&1 \
  || { cat "${TMP}/backup-prerelease.log" >&2; fail "pre-release backup (local-only) failed"; }
daily_local="$(first_dump_basename "${BACKUPS_DIR}/daily")"
pre_local="$(dump_basenames "${BACKUPS_DIR}/pre-release" | grep -- "-${RELEASE_SHA}\\.dump\\.age$" | head -n 1 || true)"
# grep exit 1 means no match — fail below if empty.
monthly_local="$(first_dump_basename "${BACKUPS_DIR}/monthly")"
[[ -n "$daily_local" ]] || fail "no local daily backup object"
[[ -n "$pre_local" ]] || fail "no local pre-release backup object"
[[ -n "$monthly_local" ]] || fail "no local monthly backup object"
assert_dump_owner_mode "daily/${daily_local}"
decrypt_dump_ok "${BACKUPS_DIR}/daily/${daily_local}"
decrypt_dump_ok "${BACKUPS_DIR}/pre-release/${pre_local}"
"${compose[@]}" run --rm -T --no-deps \
  -v "${TMP}/age.key:/run/age.key:ro" \
  -e SP_BACKUP_AGE_IDENTITY_FILE=/run/age.key \
  --entrypoint restore-verify.sh backup
echo "prod-smoke: backup_local_only_ok"

echo "prod-smoke: backups segment 2 (local rclone remote sp)"
wipe_backups_bind
# rclone type=local uses the path after "sp:" as a host path inside the container.
OFFSITE_REMOTE_PATH="/backups/.offsite-sp/svoi-pravila"
prepare_backup_dirs
cat >"${BACKUP_REMOTE_DIR}/rclone.conf" <<'EOF'
[sp]
type = local
EOF
chmod 0600 "${BACKUP_REMOTE_DIR}/rclone.conf"
# Seed past retention so the next run prunes to keep counts 30 / 12 / 10.
seed_named_dumps "${BACKUPS_DIR}/daily" 30 "202001"
seed_named_dumps "${BACKUPS_DIR}/monthly" 12 "202002"
seed_named_dumps "${BACKUPS_DIR}/pre-release" 10 "202003"
cp -a "${BACKUPS_DIR}/daily/." "${OFFSITE_DIR}/svoi-pravila/daily/"
cp -a "${BACKUPS_DIR}/monthly/." "${OFFSITE_DIR}/svoi-pravila/monthly/"
cp -a "${BACKUPS_DIR}/pre-release/." "${OFFSITE_DIR}/svoi-pravila/pre-release/"

"${compose[@]}" run --rm -T -e "SP_BACKUP_REMOTE_PATH=${OFFSITE_REMOTE_PATH}" backup \
  >"${TMP}/backup-offsite.log" 2>&1 \
  || { cat "${TMP}/backup-offsite.log" >&2; fail "daily backup (with offsite) failed"; }
grep -F "$OFFSITE_WARN" "${TMP}/backup-offsite.log" >/dev/null \
  && fail "offsite-configured run must not warn about missing offsite"
"${compose[@]}" run --rm -T \
  -e "SP_RELEASE_SHA=${RELEASE_SHA}" \
  -e "SP_BACKUP_REMOTE_PATH=${OFFSITE_REMOTE_PATH}" \
  backup --pre-release \
  >"${TMP}/backup-offsite-pre.log" 2>&1 \
  || { cat "${TMP}/backup-offsite-pre.log" >&2; fail "pre-release backup (with offsite) failed"; }

[[ "$(count_age "${BACKUPS_DIR}/daily")" == "30" ]] \
  || fail "local daily retention expected 30, got $(count_age "${BACKUPS_DIR}/daily")"
[[ "$(count_age "${BACKUPS_DIR}/monthly")" == "12" ]] \
  || fail "local monthly retention expected 12, got $(count_age "${BACKUPS_DIR}/monthly")"
[[ "$(count_age "${BACKUPS_DIR}/pre-release")" == "10" ]] \
  || fail "local pre-release retention expected 10, got $(count_age "${BACKUPS_DIR}/pre-release")"
[[ "$(count_age "${OFFSITE_DIR}/svoi-pravila/daily")" == "30" ]] \
  || fail "offsite daily retention expected 30"
[[ "$(count_age "${OFFSITE_DIR}/svoi-pravila/monthly")" == "12" ]] \
  || fail "offsite monthly retention expected 12"
[[ "$(count_age "${OFFSITE_DIR}/svoi-pravila/pre-release")" == "10" ]] \
  || fail "offsite pre-release retention expected 10"

newest_daily="$(newest_dump_basename "${BACKUPS_DIR}/daily")"
newest_offsite="$(newest_dump_basename "${OFFSITE_DIR}/svoi-pravila/daily")"
[[ -n "$newest_daily" && "$newest_daily" == "$newest_offsite" ]] \
  || fail "local and offsite newest daily names differ"
assert_dump_owner_mode "daily/${newest_daily}"
decrypt_dump_ok "${BACKUPS_DIR}/daily/${newest_daily}"
decrypt_dump_ok "${OFFSITE_DIR}/svoi-pravila/daily/${newest_offsite}"
"${compose[@]}" run --rm -T --no-deps \
  -v "${TMP}/age.key:/run/age.key:ro" \
  -e SP_BACKUP_AGE_IDENTITY_FILE=/run/age.key \
  --entrypoint restore-verify.sh backup
"${compose[@]}" run --rm -T --no-deps \
  -v "${TMP}/age.key:/run/age.key:ro" \
  -e SP_BACKUP_AGE_IDENTITY_FILE=/run/age.key \
  -e "SP_BACKUP_REMOTE_PATH=${OFFSITE_REMOTE_PATH}" \
  --entrypoint restore-verify.sh backup --remote
echo "prod-smoke: backup_offsite_ok"

echo "prod-smoke: PASS"
