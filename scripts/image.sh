#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

docker buildx build --load -t svoi-pravila:ci -f "$ROOT/backend/Dockerfile" "$ROOT/backend"
docker buildx build --load -t svoi-pravila-miniapp:ci -f "$ROOT/miniapp/Dockerfile" "$ROOT/miniapp"
docker buildx build --load -t svoi-pravila-grafana:ci -f "$ROOT/ops/grafana/Dockerfile" "$ROOT/ops/grafana"
