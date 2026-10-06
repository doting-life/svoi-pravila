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

# Grafana OSS (profile observability); digest must stay in sync with compose.yaml.
GRAFANA_IMAGE="grafana/grafana:13.2.3-slim@sha256:7c2b05e94eb43e2b88aa814d7a1c9421affc7f3593a951148177f29feaff6017"
docker pull "$GRAFANA_IMAGE"
scan "$GRAFANA_IMAGE"
