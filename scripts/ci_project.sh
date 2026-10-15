#!/usr/bin/env bash
# Sourced by ci.sh. Names the per-run resources of one `make ci` run.
#
# Everything is derived from the unique suffix of the run's temporary directory
# (`svoi-pravila-ci.<suffix>`), so concurrent runs never share containers,
# networks, volumes or local image tags.

# Prints the lower-cased unique suffix of the run's temporary directory.
ci_run_suffix() {
  local ci_tmpdir="$1"
  local base suffix
  base="$(basename "$ci_tmpdir")"
  suffix="$(printf '%s' "${base##*.}" | tr '[:upper:]' '[:lower:]')"
  if [[ -z "$suffix" || "$suffix" == "$(printf '%s' "$base" | tr '[:upper:]' '[:lower:]')" ]]; then
    echo "ci: CI_TMPDIR has no unique suffix: ${ci_tmpdir}" >&2
    return 1
  fi
  printf '%s\n' "$suffix"
}

# Prints the compose project name of the run. An explicit COMPOSE_PROJECT_NAME wins.
ci_project_name() {
  local ci_tmpdir="$1"
  if [[ -n "${COMPOSE_PROJECT_NAME:-}" ]]; then
    printf '%s\n' "$COMPOSE_PROJECT_NAME"
    return 0
  fi
  local suffix
  suffix="$(ci_run_suffix "$ci_tmpdir")" || return 1
  printf 'svoi-pravila-ci-%s\n' "$suffix"
}

# Prints the tag of the run's four local images.
ci_image_tag() {
  local suffix
  suffix="$(ci_run_suffix "$1")" || return 1
  printf 'ci-%s\n' "$suffix"
}

# Prints the tag of the images compose builds from this repository.
ci_local_image_tag() {
  local suffix
  suffix="$(ci_run_suffix "$1")" || return 1
  printf 'local-%s\n' "$suffix"
}
