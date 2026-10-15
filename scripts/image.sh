#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# Tag of the four local images; make ci sets a per-run tag, standalone use keeps `ci`.
IMAGE_TAG="${IMAGE_TAG:-ci}"

docker buildx build --load -t "svoi-pravila:${IMAGE_TAG}" -f "$ROOT/backend/Dockerfile" "$ROOT/backend"
docker buildx build --load -t "svoi-pravila-miniapp:${IMAGE_TAG}" -f "$ROOT/miniapp/Dockerfile" "$ROOT/miniapp"
docker buildx build --load -t "svoi-pravila-grafana:${IMAGE_TAG}" -f "$ROOT/ops/grafana/Dockerfile" "$ROOT/ops/grafana"
docker buildx build --load -t "svoi-pravila-backup:${IMAGE_TAG}" -f "$ROOT/deploy/backup/Dockerfile" "$ROOT/deploy/backup"
