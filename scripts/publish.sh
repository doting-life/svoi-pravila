#!/usr/bin/env bash
# Push the locally built CI images to GHCR tagged with the git sha and print their digests.
# Expects `make image` to have produced the svoi-pravila*:${IMAGE_TAG} images (default tag `ci`).
#
# Environment:
#   GHCR_TOKEN or GITHUB_TOKEN   token with packages:write (GHCR_TOKEN wins)
#   GHCR_USER or GITHUB_ACTOR    registry login name
#   GHCR_OWNER or GITHUB_REPOSITORY_OWNER   registry namespace (lowercased)
#   GITHUB_SHA                   commit to tag with (default: git HEAD)
#   IMAGE_TAG                    tag of the local images (default: ci)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

REGISTRY="ghcr.io"
IMAGE_TAG="${IMAGE_TAG:-ci}"
# local image name : published name (the local tag is IMAGE_TAG)
IMAGES=(
  "svoi-pravila:svoi-pravila-api"
  "svoi-pravila-miniapp:svoi-pravila-miniapp"
  "svoi-pravila-grafana:svoi-pravila-grafana"
  "svoi-pravila-backup:svoi-pravila-backup"
)

die() {
  echo "publish: $*" >&2
  exit 1
}

token="${GHCR_TOKEN:-${GITHUB_TOKEN:-}}"
user="${GHCR_USER:-${GITHUB_ACTOR:-}}"
owner_raw="${GHCR_OWNER:-${GITHUB_REPOSITORY_OWNER:-}}"
sha="${GITHUB_SHA:-$(git rev-parse HEAD)}"

[[ -n "$token" ]] || die "GHCR_TOKEN or GITHUB_TOKEN is required"
[[ -n "$user" ]] || die "GHCR_USER or GITHUB_ACTOR is required"
[[ -n "$owner_raw" ]] || die "GHCR_OWNER or GITHUB_REPOSITORY_OWNER is required"
[[ "$sha" =~ ^[0-9a-f]{40}$ ]] || die "git sha must be 40 lowercase hex characters"
owner="$(printf '%s' "$owner_raw" | tr '[:upper:]' '[:lower:]')"

printf '%s' "$token" | docker login "$REGISTRY" --username "$user" --password-stdin >/dev/null

for entry in "${IMAGES[@]}"; do
  local_image="${entry%%:*}:${IMAGE_TAG}"
  published="${entry##*:}"
  repository="${REGISTRY}/${owner}/${published}"
  docker image inspect "$local_image" >/dev/null 2>&1 \
    || die "${local_image} is missing; run make image first"
  docker tag "$local_image" "${repository}:${sha}"
  docker push --quiet "${repository}:${sha}" >/dev/null
  digest_ref="$(docker image inspect --format '{{range .RepoDigests}}{{println .}}{{end}}' \
    "${repository}:${sha}" | grep -F "${repository}@sha256:" | head -n 1)"
  [[ -n "$digest_ref" ]] || die "no registry digest recorded for ${repository}:${sha}"
  echo "$digest_ref"
done
