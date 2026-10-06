#!/usr/bin/env bash
set -euo pipefail

if [[ -z "${BASE:-}" ]]; then
  echo "ownership-guard: BASE is required" >&2
  exit 1
fi

git fetch origin "$BASE"
if git diff --name-only "origin/${BASE}...HEAD" | grep -E '^(AGENTS\.md|\.cursor/|docs/maintainers/)'; then
  echo "Task branches must not modify CTO-owned paths." >&2
  exit 1
fi
