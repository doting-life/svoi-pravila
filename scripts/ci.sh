#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# shellcheck source=scripts/image-pins.env
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

CI_LOG_DIR="$ROOT/.ci-logs"
rm -rf "$CI_LOG_DIR"
mkdir -p "$CI_LOG_DIR"

if [[ "$(cd "$(dirname "$ENV_FILE")" && pwd)/$(basename "$ENV_FILE")" == "$REPO_ENV" ]]; then
  echo "make ci must not use the repository .env" >&2
  exit 1
fi

cleanup() {
  docker compose -p "$COMPOSE_PROJECT_NAME" --env-file "$ENV_FILE" \
    --profile app --profile observability down -v >/dev/null 2>&1 || true
  docker compose -p "$COMPOSE_PROJECT_NAME" --env-file "$ENV_FILE" down -v >/dev/null 2>&1 || true
  rm -rf "$CI_TMPDIR"
}
trap cleanup EXIT

run_stage() {
  local job="$1"
  local stage="$2"
  shift 2
  local log="${CI_LOG_DIR}/${job}-${stage}.log"
  local start end rc
  start="$(date +%s)"
  set +e
  ( set -euo pipefail; "$@" ) >"$log" 2>&1
  rc=$?
  set -e
  end="$(date +%s)"
  if [[ "$rc" -eq 0 ]]; then
    echo "ci: ${job} ${stage} PASS $((end - start))s"
  else
    echo "ci: ${job} ${stage} FAIL $((end - start))s"
    echo "---- diagnostic lines of ${log} ----"
    # Prefer quiet fail markers so bulky compose dumps do not hide the reason.
    if grep -E '^(prod-smoke:|backup:|stack-smoke:)' "$log" >/dev/null 2>&1; then
      grep -E '^(prod-smoke:|backup:|stack-smoke:|.*Error|.*FAIL|.*Permission denied)' "$log" \
        | tail -n 40 || true
    else
      tail -n 40 "$log" || true
    fi
    echo "---- last 20 lines of ${log} ----"
    tail -n 20 "$log" || true
    echo "---- log: ${log} ----"
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

uv_sync() {
  (
    cd "$ROOT/backend"
    uv sync --locked
  )
}

job_workflow() {
  run_stage workflow actionlint \
    docker run --rm -v "$ROOT:/repo:ro" --workdir /repo "$ACTIONLINT_IMAGE" -color
  run_stage workflow parity \
    bash -c "cd \"${ROOT}/backend\" && uv run --locked python ../scripts/ci_parity_check.py"
}

job_toolchain() {
  make -C "$ROOT" toolchain-check
}

job_backend() {
  run_stage backend toolchain job_toolchain
  run_stage backend sync uv_sync
  run_stage backend materialize materialize
  run_stage backend compose compose_infra
  run_stage backend lint make -C "$ROOT" lint ENV_FILE="$ENV_FILE"
  run_stage backend fmt-check make -C "$ROOT" fmt-check ENV_FILE="$ENV_FILE"
  run_stage backend typecheck make -C "$ROOT" typecheck ENV_FILE="$ENV_FILE"
  run_stage backend imports make -C "$ROOT" imports ENV_FILE="$ENV_FILE"
  run_stage backend migrations-check make -C "$ROOT" migrations-check ENV_FILE="$ENV_FILE"
  run_stage backend test make -C "$ROOT" test ENV_FILE="$ENV_FILE"
  run_stage backend audit make -C "$ROOT" audit ENV_FILE="$ENV_FILE"
}

job_miniapp() {
  run_stage miniapp toolchain job_toolchain
  run_stage miniapp sync uv_sync
  run_stage miniapp install make -C "$ROOT" miniapp-install
  run_stage miniapp check make -C "$ROOT" miniapp-check
}

job_secrets() {
  run_stage secrets toolchain job_toolchain
  run_stage secrets shellcheck make -C "$ROOT" shellcheck
  run_stage secrets scan make -C "$ROOT" secrets
}

job_image() {
  run_stage image toolchain job_toolchain
  run_stage image build make -C "$ROOT" image
  run_stage image scan make -C "$ROOT" image-scan
}

job_stack_smoke() {
  run_stage stack-smoke toolchain job_toolchain
  run_stage stack-smoke materialize materialize stack-smoke
  run_stage stack-smoke smoke make -C "$ROOT" stack-smoke ENV_FILE="$ENV_FILE"
}

job_prod_smoke() {
  run_stage prod-smoke toolchain job_toolchain
  run_stage prod-smoke build make -C "$ROOT" image
  run_stage prod-smoke smoke make -C "$ROOT" prod-smoke
}

job_publish() {
  run_stage publish toolchain job_toolchain
  run_stage publish build make -C "$ROOT" image
  run_stage publish scan make -C "$ROOT" image-scan
  run_stage publish push make -C "$ROOT" publish
  # Digests are captured into the stage log; echo them for RUNBOOK §5 / the quiet job log.
  local digest_log="${CI_LOG_DIR}/publish-push.log"
  local digests
  if ! digests="$(grep -E '^ghcr\.io/[^[:space:]]+@sha256:[0-9a-f]+$' "$digest_log")"; then
    echo "ci: publish digests missing from ${digest_log}" >&2
    exit 1
  fi
  echo "ci: publish digests"
  printf '%s\n' "$digests"
  if [[ -n "${GITHUB_STEP_SUMMARY:-}" ]]; then
    {
      echo "### Published image digests"
      echo
      local bt='`'
      while IFS= read -r line; do
        printf -- '- %s%s%s\n' "$bt" "$line" "$bt"
      done <<<"$digests"
    } >>"$GITHUB_STEP_SUMMARY"
  fi
}

job_ownership_guard() {
  run_stage ownership-guard toolchain job_toolchain
  run_stage ownership-guard check make -C "$ROOT" ownership-guard BASE="$BASE"
}

run_job() {
  local name="$1"
  local fn="job_${name//-/_}"
  if ! declare -F "$fn" >/dev/null; then
    echo "unknown JOB=${name}" >&2
    exit 1
  fi
  "$fn"
}

run_stage setup materialize materialize
job_workflow

if [[ -n "${JOB:-}" ]]; then
  run_job "$JOB"
  exit 0
fi

for name in "${ALL_JOBS[@]}"; do
  run_job "$name"
done
