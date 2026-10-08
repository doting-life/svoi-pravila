#!/usr/bin/env bash
# Run shellcheck (--severity=style) over every project shell script via a pinned image.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# shellcheck source=scripts/image-pins.env
source "$ROOT/scripts/image-pins.env"

files=()
while IFS= read -r path; do
  [[ -n "$path" ]] || continue
  files+=("$path")
done < <(
  {
    find deploy scripts -type f -name '*.sh'
    find .githooks -type f ! -name '.*'
  } | sort
)

[[ "${#files[@]}" -gt 0 ]] || {
  echo "shellcheck: no scripts found" >&2
  exit 1
}

docker run --rm \
  -v "${ROOT}:/repo:ro" \
  -w /repo \
  "$SHELLCHECK_IMAGE" \
  -x \
  --severity=style \
  "${files[@]}"

echo "shellcheck: ok (${#files[@]} files)"
