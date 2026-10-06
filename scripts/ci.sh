#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

run_stage() {
  local name="$1"
  shift
  local start end rc
  start="$(date +%s)"
  set +e
  "$@"
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

run_stage toolchain-check make toolchain-check
if [[ ! -f "$ROOT/.env" ]]; then
  ENV_FILE="$(mktemp "${TMPDIR:-/tmp}/svoi-pravila-ci.XXXXXX.env")"
  export ENV_FILE
  make infra-up
fi
run_stage check make check
run_stage secrets make secrets
run_stage image make image
run_stage image-scan make image-scan
run_stage stack-smoke make stack-smoke
run_stage ownership-guard make ownership-guard BASE=master
