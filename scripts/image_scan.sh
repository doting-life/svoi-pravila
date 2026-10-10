#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# shellcheck source=scripts/image-pins.env
source "$ROOT/scripts/image-pins.env"

CACHE_DIR="${XDG_CACHE_HOME:-$HOME/.cache}/svoi-pravila/trivy"
mkdir -p "$CACHE_DIR"

scan() {
  local image="$1"
  docker run --rm \
    -v /var/run/docker.sock:/var/run/docker.sock \
    -v "$CACHE_DIR:/root/.cache/trivy" \
    "$TRIVY_IMAGE" \
    image --severity HIGH,CRITICAL --ignore-unfixed --exit-code 1 --format table \
    "$image"
}

scan svoi-pravila:ci
scan svoi-pravila-miniapp:ci
# Built Grafana image (plugin baked in); not the upstream base alone.
scan svoi-pravila-grafana:ci
scan svoi-pravila-backup:ci
# Prometheus rebuilt from source (ADR-0008-style) for current Go/x/net CVEs.
scan svoi-pravila-prometheus:ci
