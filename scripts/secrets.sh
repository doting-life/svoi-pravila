#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# shellcheck source=scripts/image-pins.env
source "$ROOT/scripts/image-pins.env"

docker run --rm -v "$ROOT:/repo:ro" "$GITLEAKS_IMAGE" detect --source=/repo --verbose
