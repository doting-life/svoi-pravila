#!/usr/bin/env bash
# Deploy one published release to this server:
#   deploy/release.sh <git-sha>
#
# Checks out that commit, pulls the four images tagged with it from ghcr.io, pins them by digest
# in /srv/svoi-pravila/releases/<sha>.env, takes a pre-release backup, runs migrations, starts the
# stack and verifies it (API readiness, Telegram webhook, HTTPS with CSP and HSTS).
# On failure it prints the rollback command. Run it as the deploy user.
#
# Internal: `--rollback <sha>` (used by rollback.sh) redeploys an older release without running
# migrations; schema changes must therefore stay backward compatible (see deploy/RUNBOOK.md).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
APP_DIR="/srv/svoi-pravila"
RELEASES_DIR="${APP_DIR}/releases"
STATE_DIR="${APP_DIR}/state"
ENV_FILE="/etc/svoi-pravila/env"
GHCR_TOKEN_FILE="/etc/svoi-pravila/ghcr-token"
EXAMPLE_ENV="${SCRIPT_DIR}/env.prod.example"
REGISTRY="ghcr.io"
IMAGE_NAMES=(api miniapp grafana backup)
MIN_FREE_KB=$((3 * 1024 * 1024))
EXPECTED_HSTS="max-age=31536000; includeSubDomains"
export SP_ENV_FILE="$ENV_FILE"

log() {
  printf 'release: %s\n' "$*"
}

die() {
  printf 'release: %s\n' "$*" >&2
  exit 1
}

rollback_mode=0
if [[ "${1:-}" == "--rollback" ]]; then
  rollback_mode=1
  shift
fi
[[ $# -eq 1 ]] || die "usage: release.sh <git-sha>"
sha="$1"
[[ "$sha" =~ ^[0-9a-f]{40}$ ]] || die "git sha must be 40 lowercase hex characters"
ORIGINAL_ARGS=("$@")
if [[ "$rollback_mode" -eq 1 ]]; then
  ORIGINAL_ARGS=(--rollback "$sha")
fi

if [[ -z "${SP_RELEASE_REEXEC:-}" ]]; then
  command -v git >/dev/null || die "git is required"
  [[ -z "$(git -C "$REPO_DIR" status --porcelain)" ]] || die "the repository checkout has local changes"
  git -C "$REPO_DIR" fetch --quiet --tags origin
  git -C "$REPO_DIR" merge-base --is-ancestor "$sha" origin/master \
    || die "${sha} is not on origin/master"
  git -C "$REPO_DIR" checkout --quiet --detach "$sha"
  SP_RELEASE_REEXEC=1 exec "${REPO_DIR}/deploy/release.sh" "${ORIGINAL_ARGS[@]}"
fi

DEPLOY_DIR="${REPO_DIR}/deploy"
RELEASE_ENV="${RELEASES_DIR}/${sha}.env"
rollout_started=0

compose() {
  docker compose --project-directory "$DEPLOY_DIR" -f "${DEPLOY_DIR}/compose.prod.yaml" \
    --env-file "$ENV_FILE" --env-file "$RELEASE_ENV" "$@"
}

env_value() {
  grep -E "^$2=" "$1" | tail -n 1 | cut -d= -f2-
}

on_exit() {
  local rc=$?
  if [[ "$rc" -eq 0 ]]; then
    return 0
  fi
  if [[ "$rollout_started" -eq 1 ]]; then
    {
      echo "release: FAILED for ${sha} (exit ${rc}). Recent service logs:"
      compose logs --no-color --tail 40 api migrate miniapp || true
      echo "release: roll back to the previous release with:"
      echo "release:   ${DEPLOY_DIR}/rollback.sh"
    } >&2
  else
    echo "release: FAILED for ${sha} before anything was changed on this server (exit ${rc})" >&2
  fi
  return "$rc"
}
trap on_exit EXIT

preflight() {
  local tool key free_kb
  for tool in docker curl jq git awk; do
    command -v "$tool" >/dev/null || die "${tool} is required"
  done
  docker compose version >/dev/null 2>&1 || die "docker compose plugin is required"
  docker info >/dev/null 2>&1 || die "cannot talk to Docker; is this user in the docker group?"
  [[ -f "$ENV_FILE" ]] || die "${ENV_FILE} is missing"
  [[ "$(stat -c %a "$ENV_FILE")" == "600" ]] || die "${ENV_FILE} must have mode 600"
  if grep -qE '^[A-Z][A-Z0-9_]*=CHANGE_ME$' "$ENV_FILE"; then
    die "${ENV_FILE} still contains CHANGE_ME values"
  fi
  while IFS= read -r key; do
    [[ -n "$(env_value "$ENV_FILE" "$key")" ]] || die "${key} is missing or empty in ${ENV_FILE}"
  done < <(grep -oE '^[A-Z][A-Z0-9_]*=' "$EXAMPLE_ENV" | tr -d '=')
  [[ "$(env_value "$ENV_FILE" SP_ENVIRONMENT)" == "production" ]] \
    || die "SP_ENVIRONMENT must be production"
  [[ -s "$GHCR_TOKEN_FILE" ]] || die "${GHCR_TOKEN_FILE} is missing or empty"
  [[ "$(stat -c %a "$GHCR_TOKEN_FILE")" == "600" ]] || die "${GHCR_TOKEN_FILE} must have mode 600"
  free_kb="$(df -Pk /var/lib/docker | awk 'NR == 2 { print $4 }')"
  [[ "$free_kb" -ge "$MIN_FREE_KB" ]] || die "less than 3 GiB free on /var/lib/docker"
  local backups_dir="/srv/svoi-pravila/backups"
  [[ -d "$backups_dir" ]] || die "${backups_dir} is missing (create it mode 700 owned by deploy)"
  [[ "$(stat -c %a "$backups_dir")" == "700" ]] || die "${backups_dir} must have mode 700"
  local backup_free_kb newest_bytes needed_kb twice_kb
  backup_free_kb="$(df -Pk "$backups_dir" | awk 'NR == 2 { print $4 }')"
  newest_bytes=0
  if compgen -G "${backups_dir}/*/*.dump.age" >/dev/null; then
    newest_bytes="$(find "$backups_dir" -type f -name '*.dump.age' -printf '%s\n' \
      | sort -n | tail -n 1)"
  fi
  needed_kb="$MIN_FREE_KB"
  if [[ "$newest_bytes" =~ ^[0-9]+$ && "$newest_bytes" -gt 0 ]]; then
    twice_kb=$(((newest_bytes * 2 + 1023) / 1024))
    if [[ "$twice_kb" -gt "$needed_kb" ]]; then
      needed_kb="$twice_kb"
    fi
  fi
  [[ "$backup_free_kb" -ge "$needed_kb" ]] \
    || die "less than ${needed_kb} KiB free on ${backups_dir} (need max(3 GiB, 2× newest dump))"
  install -d -m 0750 "$RELEASES_DIR" "$STATE_DIR"
}

registry_login() {
  docker login "$REGISTRY" --username "$(env_value "$ENV_FILE" SP_GHCR_USER)" \
    --password-stdin <"$GHCR_TOKEN_FILE" >/dev/null
}

pull_digest() {
  local reference="$1" digest
  docker pull --quiet "$reference" >/dev/null
  digest="$(docker image inspect --format '{{range .RepoDigests}}{{println .}}{{end}}' "$reference" \
    | grep -F "${reference%%:*}@sha256:" | head -n 1)"
  [[ -n "$digest" ]] || die "no registry digest for ${reference}"
  printf '%s' "$digest"
}

write_release_env() {
  local owner name digest tmp upper
  owner="$(env_value "$ENV_FILE" SP_GHCR_OWNER)"
  tmp="$(mktemp "${RELEASES_DIR}/.${sha}.XXXXXX")"
  for name in "${IMAGE_NAMES[@]}"; do
    digest="$(pull_digest "${REGISTRY}/${owner}/svoi-pravila-${name}:${sha}")"
    upper="$(printf '%s' "$name" | tr '[:lower:]' '[:upper:]')"
    printf '%s_IMAGE=%s\n' "$upper" "$digest" >>"$tmp"
  done
  printf 'PROMETHEUS_IMAGE=%s\n' "$(env_value "${REPO_DIR}/scripts/image-pins.env" PROMETHEUS_IMAGE)" >>"$tmp"
  chmod 0640 "$tmp"
  mv "$tmp" "$RELEASE_ENV"
}

pin_release() {
  if [[ -f "$RELEASE_ENV" ]]; then
    log "reusing the pinned digests in ${RELEASE_ENV}"
    local line
    while IFS= read -r line; do
      docker pull --quiet "${line#*=}" >/dev/null
    done <"$RELEASE_ENV"
  else
    write_release_env
  fi
  compose config --quiet
  log "pinned digests:"
  sed 's/^/release:   /' "$RELEASE_ENV"
}

record_state() {
  local current=""
  if [[ -f "${STATE_DIR}/current" ]]; then
    current="$(<"${STATE_DIR}/current")"
  fi
  if [[ -n "$current" && "$current" != "$sha" ]]; then
    printf '%s\n' "$current" >"${STATE_DIR}/previous"
  fi
  printf '%s\n' "$sha" >"${STATE_DIR}/current"
  ln -sfn "../releases/${sha}.env" "${STATE_DIR}/current.env"
}

wait_until() {
  local seconds="$1" description="$2"
  shift 2
  local deadline=$((SECONDS + seconds))
  until "$@" >/dev/null 2>&1; do
    if ((SECONDS >= deadline)); then
      die "timed out after ${seconds}s waiting for ${description}"
    fi
    sleep 3
  done
}

api_ready() {
  compose exec -T api svoi-pravila-healthcheck
}

csp_from_caddyfile() {
  sed -n 's/.*Content-Security-Policy "\([^"]*\)".*/\1/p' "${REPO_DIR}/miniapp/Caddyfile.prod" | head -n 1
}

https_headers_ok() {
  local domain headers expected_csp got
  domain="$(env_value "$ENV_FILE" SP_DOMAIN)"
  headers="$(curl -sS --max-time 10 -D - -o /dev/null "https://${domain}/")" || return 1
  expected_csp="$(csp_from_caddyfile)"
  got="$(printf '%s\n' "$headers" | awk -F': ' 'tolower($1) == "strict-transport-security" { sub(/\r$/, "", $2); print $2; exit }')"
  [[ "$got" == "$EXPECTED_HSTS" ]] || return 1
  got="$(printf '%s\n' "$headers" | awk -F': ' 'tolower($1) == "content-security-policy" { sub(/\r$/, "", $2); print $2; exit }')"
  [[ -n "$expected_csp" && "$got" == "$expected_csp" ]]
}

webhook_info() {
  printf 'url = "https://api.telegram.org/bot%s/getWebhookInfo"\n' \
    "$(env_value "$ENV_FILE" SP_TELEGRAM_BOT_TOKEN)" | curl -fsS --max-time 10 -K -
}

webhook_registered() {
  local expected info
  expected="https://$(env_value "$ENV_FILE" SP_DOMAIN)/tg/$(env_value "$ENV_FILE" SP_TELEGRAM_WEBHOOK_PATH_SECRET)"
  info="$(webhook_info)" || return 1
  [[ "$(jq -r '.result.url' <<<"$info")" == "$expected" ]]
}

run_checks() {
  wait_until 120 "the API readiness probe" api_ready
  log "api ready: ok"
  wait_until 240 "HTTPS with the expected CSP and HSTS headers" https_headers_ok
  log "https csp and hsts: ok"
  wait_until 60 "the Telegram webhook to point at this server" webhook_registered
  log "telegram webhook registered: ok"
  local info
  info="$(webhook_info)"
  log "webhook pending updates: $(jq -r '.result.pending_update_count' <<<"$info")"
  log "webhook last error: $(jq -r '.result.last_error_message // "none"' <<<"$info")"
}

preflight
registry_login
pin_release

rollout_started=1
record_state
compose up -d --wait postgres valkey
log "taking the pre-release backup"
SP_RELEASE_SHA="$sha" compose run --rm -T backup --pre-release

if [[ "$rollback_mode" -eq 1 ]]; then
  log "rollback: skipping migrations; the database stays at its current schema"
  compose up -d --wait --wait-timeout 300 --no-deps api miniapp prometheus grafana
else
  log "running migrations"
  compose run --rm -T migrate
  compose up -d --wait --wait-timeout 300 --remove-orphans
fi

run_checks
log "release ${sha} is live"
