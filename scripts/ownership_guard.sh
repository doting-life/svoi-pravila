#!/usr/bin/env bash
set -euo pipefail

if [[ -z "${BASE:-}" ]]; then
  echo "ownership-guard: BASE is required" >&2
  exit 1
fi

ref="${PUSH_REF:-$(git symbolic-ref --short HEAD 2>/dev/null || true)}"
ref="${ref#refs/heads/}"
if [[ "${GITHUB_EVENT_NAME:-}" != "pull_request" && "$ref" != task/* ]]; then
  echo "ownership-guard: skip (not a task branch)"
  exit 0
fi

git fetch origin "$BASE"
if git diff --name-only "origin/${BASE}...HEAD" | grep -E '^(AGENTS\.md|\.cursor/|docs/maintainers/)'; then
  echo "Task branches must not modify CTO-owned paths." >&2
  exit 1
fi
