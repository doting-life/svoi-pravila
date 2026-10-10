#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# shellcheck source=scripts/image-pins.env
source "$ROOT/scripts/image-pins.env"

CACHE_DIR="${XDG_CACHE_HOME:-$HOME/.cache}/svoi-pravila/trivy"
mkdir -p "$CACHE_DIR"

EXCEPTIONS="${ROOT}/ops/trivy-exceptions.yaml"

(
  cd "$ROOT/backend"
  uv run --locked python ../scripts/trivy_exceptions.py --exceptions "$EXCEPTIONS" --validate
)

scan_blocking() {
  local image="$1"
  docker run --rm \
    -v /var/run/docker.sock:/var/run/docker.sock \
    -v "$CACHE_DIR:/root/.cache/trivy" \
    "$TRIVY_IMAGE" \
    image --severity HIGH,CRITICAL --ignore-unfixed --exit-code 1 --format table \
    "$image"
}

scan_with_exceptions() {
  local logical="$1"
  local image="$2"
  local json
  json="$(
    docker run --rm \
      -v /var/run/docker.sock:/var/run/docker.sock \
      -v "$CACHE_DIR:/root/.cache/trivy" \
      "$TRIVY_IMAGE" \
      image --severity HIGH,CRITICAL --ignore-unfixed --exit-code 0 --format json \
      "$image"
  )"
  (
    cd "$ROOT/backend"
    printf '%s' "$json" | uv run --locked python ../scripts/trivy_exceptions.py \
      --exceptions "$EXCEPTIONS" --image "$logical"
  )
}

scan_blocking svoi-pravila:ci
scan_blocking svoi-pravila-miniapp:ci
# Built Grafana image (plugin baked in); not the upstream base alone.
scan_with_exceptions grafana svoi-pravila-grafana:ci
scan_blocking svoi-pravila-backup:ci
# Upstream Prometheus (digest-pinned in compose / image-pins.env).
docker pull "$PROMETHEUS_IMAGE"
scan_with_exceptions prometheus "$PROMETHEUS_IMAGE"
