#!/usr/bin/env bash
# Redeploy the previous release recorded by release.sh. Migrations are not reverted: the previous
# code must run against the newer schema, which is why migrations stay backward compatible.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PREVIOUS_FILE="/srv/svoi-pravila/state/previous"

[[ -s "$PREVIOUS_FILE" ]] || {
  echo "rollback: no previous release is recorded in ${PREVIOUS_FILE}" >&2
  exit 1
}
previous="$(<"$PREVIOUS_FILE")"
echo "rollback: redeploying ${previous}"
exec "${SCRIPT_DIR}/release.sh" --rollback "$previous"
