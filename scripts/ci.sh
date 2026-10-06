#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# shellcheck disable=SC1091
source "$ROOT/scripts/image-pins.env"

if [[ -z "${CI_JOBS:-}" ]]; then
  echo "CI_JOBS is unset" >&2
  exit 1
fi
read -r -a ALL_JOBS <<< "$CI_JOBS"

REPO_ENV="$ROOT/.env"
COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-svoi-pravila-ci}"
export COMPOSE_PROJECT_NAME
export UV_LOCKED=1
export BASE="${BASE:-master}"

PARENT="${RUNNER_TEMP:-${TMPDIR:-/tmp}}"
CI_TMPDIR="$(mktemp -d "${PARENT}/svoi-pravila-ci.XXXXXX")"
export CI_TMPDIR
ENV_FILE="${CI_TMPDIR}/env"
export ENV_FILE

if [[ "$(cd "$(dirname "$ENV_FILE")" && pwd)/$(basename "$ENV_FILE")" == "$REPO_ENV" ]]; then
  echo "make ci must not use the repository .env" >&2
  exit 1
fi

cleanup() {
  docker compose -p "$COMPOSE_PROJECT_NAME" --env-file "$ENV_FILE" --profile app down -v >/dev/null 2>&1 || true
  docker compose -p "$COMPOSE_PROJECT_NAME" --env-file "$ENV_FILE" down -v >/dev/null 2>&1 || true
  rm -rf "$CI_TMPDIR"
}
trap cleanup EXIT

run_stage() {
  local name="$1"
  shift
  local start end rc
  start="$(date +%s)"
  set +e
  ( set -euo pipefail; "$@" )
  rc=$?
  set -e
  end="$(date +%s)"
  if [[ "$rc" -eq 0 ]]; then
    echo "ci: ${name} PASS $((end - start))s"
  else
    echo "ci: ${name} FAIL $((end - start))s"
    exit "$rc"
  fi
}

materialize() {
  local extra=()
  if [[ "${1:-}" == "stack-smoke" ]]; then
    extra+=(--stack-smoke)
  fi
  (
    cd "$ROOT/backend"
    if [[ ${#extra[@]} -eq 0 ]]; then
      uv run --locked python ../scripts/materialize_ci_env.py --env-file "$ENV_FILE"
    else
      uv run --locked python ../scripts/materialize_ci_env.py --env-file "$ENV_FILE" "${extra[@]}"
    fi
  )
}

compose_infra() {
  docker compose -p "$COMPOSE_PROJECT_NAME" --env-file "$ENV_FILE" up -d --wait postgres valkey
}

job_workflow() {
  docker run --rm -v "$ROOT:/repo:ro" --workdir /repo "$ACTIONLINT_IMAGE" -color
  (
    cd "$ROOT/backend"
    uv run --locked python ../scripts/ci_parity_check.py
  )
}

job_toolchain() {
  make -C "$ROOT" toolchain-check
}

job_backend() {
  job_toolchain
  (
    cd "$ROOT/backend"
    uv sync --locked
  )
  materialize
  compose_infra
  make -C "$ROOT" lint fmt-check typecheck imports migrations-check test audit ENV_FILE="$ENV_FILE"
}

job_miniapp() {
  job_toolchain
  (
    cd "$ROOT/backend"
    uv sync --locked
  )
  make -C "$ROOT" miniapp-install miniapp-check
}

job_secrets() {
  job_toolchain
  make -C "$ROOT" secrets
}

job_image() {
  job_toolchain
  make -C "$ROOT" image image-scan
}

job_stack_smoke() {
  job_toolchain
  materialize stack-smoke
  make -C "$ROOT" stack-smoke ENV_FILE="$ENV_FILE"
}

job_ownership_guard() {
  job_toolchain
  make -C "$ROOT" ownership-guard BASE="$BASE"
}

run_job() {
  local name="$1"
  local fn="job_${name//-/_}"
  if ! declare -F "$fn" >/dev/null; then
    echo "unknown JOB=${name}" >&2
    exit 1
  fi
  run_stage "$name" "$fn"
}

materialize
run_stage workflow job_workflow

if [[ -n "${JOB:-}" ]]; then
  run_job "$JOB"
  exit 0
fi

for name in "${ALL_JOBS[@]}"; do
  run_job "$name"
done
