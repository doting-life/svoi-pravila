#!/usr/bin/env bash
# Encrypted PostgreSQL backup: pg_dump -Fc | age | rclone rcat. Plaintext never touches disk.
# Runs inside the backup image; connection and remote settings come from the environment.
set -euo pipefail

KEEP_DAILY=30
KEEP_MONTHLY=12
KEEP_PRE_RELEASE=10
NAME_RE='^svoi-pravila-[0-9]{8}T[0-9]{6}Z(-[0-9a-f]{40})?\.dump\.age$'
AGE_RECIPIENT_RE='^age1[02-9ac-hj-np-z]{58}$'

usage() {
  cat <<'EOF'
Usage: backup.sh [--pre-release]

  (no flag)        daily backup into <remote>/daily; the first daily backup of a
                   month is also kept in <remote>/monthly. Retention: 30 daily, 12 monthly.
  --pre-release    backup taken right before a release into <remote>/pre-release;
                   the release sha is read from SP_RELEASE_SHA when set.

Required environment: PGHOST PGUSER PGPASSWORD PGDATABASE
                      SP_BACKUP_AGE_RECIPIENT SP_BACKUP_REMOTE
EOF
}

log() {
  printf 'backup: %s\n' "$*"
}

die() {
  printf 'backup: %s\n' "$*" >&2
  exit 1
}

mode=daily
while [[ $# -gt 0 ]]; do
  case "$1" in
    --pre-release)
      mode=pre-release
      shift
      ;;
    -h | --help)
      usage
      exit 0
      ;;
    *)
      usage >&2
      exit 2
      ;;
  esac
done

for var in PGHOST PGUSER PGPASSWORD PGDATABASE SP_BACKUP_AGE_RECIPIENT SP_BACKUP_REMOTE; do
  [[ -n "${!var:-}" ]] || die "${var} is required"
done
[[ "$SP_BACKUP_AGE_RECIPIENT" =~ $AGE_RECIPIENT_RE ]] \
  || die "SP_BACKUP_AGE_RECIPIENT must be an age public key (age1...)"

remote="${SP_BACKUP_REMOTE%/}"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
month="${stamp:0:6}"

suffix=""
if [[ "$mode" == "pre-release" && -n "${SP_RELEASE_SHA:-}" ]]; then
  [[ "$SP_RELEASE_SHA" =~ ^[0-9a-f]{40}$ ]] || die "SP_RELEASE_SHA must be a 40-hex git sha"
  suffix="-${SP_RELEASE_SHA}"
fi
name="svoi-pravila-${stamp}${suffix}.dump.age"
dir="daily"
if [[ "$mode" == "pre-release" ]]; then
  dir="pre-release"
fi
target="${remote}/${dir}/${name}"

uploaded=0
cleanup_partial() {
  if [[ "$uploaded" -ne 1 ]]; then
    rclone deletefile "$target" >/dev/null 2>&1 || true
  fi
}
trap cleanup_partial EXIT

log "dumping ${PGDATABASE} to ${dir}/${name}"
pg_dump --format=custom --no-password \
  | age --recipient "$SP_BACKUP_AGE_RECIPIENT" \
  | rclone rcat "$target"

size="$(rclone lsf --files-only --format s --include "$name" "${remote}/${dir}")"
[[ "$size" =~ ^[0-9]+$ && "$size" -gt 0 ]] || die "uploaded object is missing or empty"
uploaded=1
log "uploaded ${dir}/${name} (${size} bytes)"

prune() {
  local prune_dir="$1"
  local keep="$2"
  local -a names=()
  local name_item
  while IFS= read -r name_item; do
    if [[ "$name_item" =~ $NAME_RE ]]; then
      names+=("$name_item")
    fi
  done < <(rclone lsf --files-only "${remote}/${prune_dir}" | sort)
  local excess=$((${#names[@]} - keep))
  if [[ "$excess" -le 0 ]]; then
    return 0
  fi
  local idx
  for ((idx = 0; idx < excess; idx++)); do
    rclone deletefile "${remote}/${prune_dir}/${names[idx]}"
    log "pruned ${prune_dir}/${names[idx]}"
  done
}

if [[ "$mode" == "daily" ]]; then
  month_existing="$(rclone lsf --files-only --include "svoi-pravila-${month}*.dump.age" \
    "${remote}/monthly" 2>/dev/null || true)"
  if [[ -z "$month_existing" ]]; then
    rclone copyto "$target" "${remote}/monthly/${name}"
    log "kept ${name} as the monthly backup for ${month}"
  fi
  prune daily "$KEEP_DAILY"
  prune monthly "$KEEP_MONTHLY"
else
  prune pre-release "$KEEP_PRE_RELEASE"
fi

log "done"
