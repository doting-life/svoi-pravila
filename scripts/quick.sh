#!/usr/bin/env bash
# Fast local checks while working. One line per step. Full CI is pre-push / make ci.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

BACKEND="$ROOT/backend"
MINIAPP="$ROOT/miniapp"

run_step() {
  local name="$1"
  shift
  local start end rc
  local log
  log="$(mktemp "${TMPDIR:-/tmp}/svoi-pravila-quick.XXXXXX")"
  start="$(date +%s)"
  set +e
  ( set -euo pipefail; "$@" ) >"$log" 2>&1
  rc=$?
  set -e
  end="$(date +%s)"
  if [[ "$rc" -eq 0 ]]; then
    rm -f "$log"
    echo "quick: ${name} PASS $((end - start))s"
  else
    echo "quick: ${name} FAIL $((end - start))s"
    echo "---- last 40 lines of ${log} ----"
    tail -n 40 "$log" || true
    echo "---- log: ${log} ----"
    exit "$rc"
  fi
}

uv_backend() {
  (
    cd "$BACKEND"
    uv run "$@"
  )
}

miniapp_tsc() {
  (
    cd "$MINIAPP"
    pnpm exec tsc --noEmit
  )
}

miniapp_eslint() {
  (
    cd "$MINIAPP"
    pnpm exec eslint --max-warnings=0 --no-warn-ignored "$@"
  )
}

miniapp_prettier() {
  (
    cd "$MINIAPP"
    pnpm exec prettier --check "$@"
  )
}

miniapp_vitest_related() {
  (
    cd "$MINIAPP"
    pnpm exec vitest related --run "$@"
  )
}

run_step ruff-format uv_backend ruff format --check src tests migrations
run_step ruff-check uv_backend ruff check src tests migrations
run_step mypy uv_backend mypy src tests
run_step pytest-unit uv_backend pytest -m "unit and not integration" -q -x

BASE="$(git merge-base HEAD origin/master)"
CHANGED=()
while IFS= read -r rel; do
  [[ -n "$rel" ]] || continue
  CHANGED+=("$rel")
done < <(
  git diff --name-only --diff-filter=ACMR "${BASE}...HEAD" -- miniapp \
    | sed 's|^miniapp/||' \
    | grep -E '\.(ts|tsx|js|jsx|mjs|cjs|css|json)$' \
    || true
)

run_step tsc miniapp_tsc

if [[ ${#CHANGED[@]} -eq 0 ]]; then
  run_step eslint true
  run_step prettier true
  run_step vitest-related true
else
  ESLINT_FILES=()
  PRETTIER_FILES=()
  for f in "${CHANGED[@]}"; do
    case "$f" in
      *.ts | *.tsx | *.js | *.jsx | *.mjs | *.cjs)
        ESLINT_FILES+=("$f")
        PRETTIER_FILES+=("$f")
        ;;
      *.css | *.json)
        PRETTIER_FILES+=("$f")
        ;;
    esac
  done

  if [[ ${#ESLINT_FILES[@]} -eq 0 ]]; then
    run_step eslint true
  else
    run_step eslint miniapp_eslint "${ESLINT_FILES[@]}"
  fi

  if [[ ${#PRETTIER_FILES[@]} -eq 0 ]]; then
    run_step prettier true
  else
    run_step prettier miniapp_prettier "${PRETTIER_FILES[@]}"
  fi

  run_step vitest-related miniapp_vitest_related "${CHANGED[@]}"
fi
