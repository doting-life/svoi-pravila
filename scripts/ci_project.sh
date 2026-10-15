#!/usr/bin/env bash
# Sourced by ci.sh. Prints the compose project name of one `make ci` run.
#
# The name carries the unique suffix of the run's temporary directory
# (`svoi-pravila-ci.<suffix>`), so concurrent runs never share containers,
# networks or volumes. An explicit COMPOSE_PROJECT_NAME wins.

ci_project_name() {
  local ci_tmpdir="$1"
  if [[ -n "${COMPOSE_PROJECT_NAME:-}" ]]; then
    printf '%s\n' "$COMPOSE_PROJECT_NAME"
    return 0
  fi
  local base suffix
  base="$(basename "$ci_tmpdir")"
  suffix="$(printf '%s' "${base##*.}" | tr '[:upper:]' '[:lower:]')"
  if [[ -z "$suffix" || "$suffix" == "$(printf '%s' "$base" | tr '[:upper:]' '[:lower:]')" ]]; then
    echo "ci: CI_TMPDIR has no unique suffix: ${ci_tmpdir}" >&2
    return 1
  fi
  printf 'svoi-pravila-ci-%s\n' "$suffix"
}
