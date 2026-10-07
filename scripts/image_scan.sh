#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# shellcheck disable=SC1091
source "$ROOT/scripts/image-pins.env"

scan() {
  local image="$1"
  docker run --rm -v /var/run/docker.sock:/var/run/docker.sock "$TRIVY_IMAGE" \
    image --severity HIGH,CRITICAL --ignore-unfixed --exit-code 1 --format table \
    "$image"
}

scan svoi-pravila:ci
scan svoi-pravila-miniapp:ci
# Built Grafana image (plugin baked in); not the upstream base alone.
scan svoi-pravila-grafana:ci
# Upstream Prometheus (digest-pinned in compose / image-pins.env).
docker pull "$PROMETHEUS_IMAGE"
scan "$PROMETHEUS_IMAGE"
