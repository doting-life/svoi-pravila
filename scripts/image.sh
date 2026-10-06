#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

docker build -t svoi-pravila:ci -f "$ROOT/backend/Dockerfile" "$ROOT/backend"
docker build -t svoi-pravila-miniapp:ci -f "$ROOT/miniapp/Dockerfile" "$ROOT/miniapp"
