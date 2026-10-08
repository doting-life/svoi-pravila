#!/usr/bin/env bash
# Encrypted PostgreSQL backup: pg_dump -Fc | age → local /backups (atomic).
# Optional offsite copy via rclone remote "sp" when /etc/svoi-pravila/rclone.conf exists.
# Plaintext never touches disk. Runs inside the backup image.
set -euo pipefail

KEEP_DAILY=30
KEEP_MONTHLY=12
KEEP_PRE_RELEASE=10
LOCAL_ROOT="${SP_BACKUP_LOCAL_ROOT:-/backups}"
RCLONE_CONF="${RCLONE_CONFIG:-/etc/svoi-pravila/rclone.conf}"
NAME_RE='^svoi-pravila-[0-9]{8}T[0-9]{6}Z(-[0-9a-f]{40})?\.dump\.age$'
AGE_RECIPIENT_RE='^age1[02-9ac-hj-np-z]{58}$'

usage() {
  cat <<'EOF'
Usage: backup.sh [--pre-release]

  (no flag)        daily backup into /backups/daily; the first daily backup of a
                   month is also kept in /backups/monthly. Retention: 30 daily, 12 monthly.
  --pre-release    backup taken right before a release into /backups/pre-release;
                   the release sha is read from SP_RELEASE_SHA when set.

  When /etc/svoi-pravila/rclone.conf defines remote "sp", the same object is also
  copied to sp:${SP_BACKUP_REMOTE_PATH}/<dir>/ with the same retention. If the
  file is absent, a warning is logged and the run succeeds (local copy only).
  A configured offsite that fails makes the run fail (local copy is kept).

Required environment: PGHOST PGUSER PGPASSWORD PGDATABASE
                      SP_BACKUP_AGE_RECIPIENT
Optional: SP_BACKUP_REMOTE_PATH (default svoi-pravila), SP_RELEASE_SHA
EOF
}

log() {
  printf 'backup: %s\n' "$*"
}

warn() {
  printf 'backup: %s\n' "$*" >&2
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

for var in PGHOST PGUSER PGPASSWORD PGDATABASE SP_BACKUP_AGE_RECIPIENT; do
  [[ -n "${!var:-}" ]] || die "${var} is required"
done
[[ "$SP_BACKUP_AGE_RECIPIENT" =~ $AGE_RECIPIENT_RE ]] \
  || die "SP_BACKUP_AGE_RECIPIENT must be an age public key (age1...)"

remote_path="${SP_BACKUP_REMOTE_PATH:-svoi-pravila}"
remote_path="${remote_path%/}"
[[ -n "$remote_path" ]] || die "SP_BACKUP_REMOTE_PATH must not be empty"

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

local_dir="${LOCAL_ROOT}/${dir}"
install -d -m 0700 "$LOCAL_ROOT" "${LOCAL_ROOT}/daily" "${LOCAL_ROOT}/monthly" "${LOCAL_ROOT}/pre-release"
final_path="${local_dir}/${name}"
tmp_path="${local_dir}/.${name}.tmp.$$"

cleanup_temp() {
  rm -f "$tmp_path"
}
trap cleanup_temp EXIT

log "dumping ${PGDATABASE} to ${dir}/${name}"
pg_dump --format=custom --no-password \
  | age --recipient "$SP_BACKUP_AGE_RECIPIENT" \
  >"$tmp_path"
[[ -s "$tmp_path" ]] || die "encrypted dump is empty"
chmod 0600 "$tmp_path"
mv -f "$tmp_path" "$final_path"
chmod 0600 "$final_path"
# Preserve host ownership of the bind mount when running as root in the container.
if [[ "$(id -u)" -eq 0 ]]; then
  chown --reference="$LOCAL_ROOT" "$final_path" 2>/dev/null \
    || chown --reference="$local_dir" "$final_path" 2>/dev/null \
    || true
fi
size="$(stat -c %s "$final_path" 2>/dev/null || stat -f %z "$final_path")"
[[ "$size" =~ ^[0-9]+$ && "$size" -gt 0 ]] || die "local dump is missing or empty"
log "wrote ${dir}/${name} (${size} bytes)"

offsite_enabled=0
if [[ -r "$RCLONE_CONF" ]] && grep -qE '^\[sp\][[:space:]]*$' "$RCLONE_CONF"; then
  offsite_enabled=1
  export RCLONE_CONFIG="$RCLONE_CONF"
elif [[ -r "$RCLONE_CONF" ]]; then
  die "rclone.conf is present but remote [sp] is not defined"
else
  warn "offsite not configured (local copy only)"
fi

prune_local() {
  local prune_dir="$1"
  local keep="$2"
  local -a names=()
  local name_item
  while IFS= read -r name_item; do
    if [[ "$name_item" =~ $NAME_RE ]]; then
      names+=("$name_item")
    fi
  done < <(ls -1 "${LOCAL_ROOT}/${prune_dir}" 2>/dev/null | sort || true)
  local excess=$((${#names[@]} - keep))
  if [[ "$excess" -le 0 ]]; then
    return 0
  fi
  local idx
  for ((idx = 0; idx < excess; idx++)); do
    rm -f "${LOCAL_ROOT}/${prune_dir}/${names[idx]}"
    log "pruned local ${prune_dir}/${names[idx]}"
  done
}

prune_remote() {
  local prune_dir="$1"
  local keep="$2"
  local remote_base="sp:${remote_path}/${prune_dir}"
  local -a names=()
  local name_item
  while IFS= read -r name_item; do
    if [[ "$name_item" =~ $NAME_RE ]]; then
      names+=("$name_item")
    fi
  done < <(rclone lsf --files-only "$remote_base" 2>/dev/null | sort || true)
  local excess=$((${#names[@]} - keep))
  if [[ "$excess" -le 0 ]]; then
    return 0
  fi
  local idx
  for ((idx = 0; idx < excess; idx++)); do
    rclone deletefile "${remote_base}/${names[idx]}"
    log "pruned offsite ${prune_dir}/${names[idx]}"
  done
}

copy_offsite() {
  local src_dir="$1"
  local object="$2"
  local dest="sp:${remote_path}/${src_dir}/"
  rclone copyto "${LOCAL_ROOT}/${src_dir}/${object}" "${dest}${object}"
  log "copied offsite ${src_dir}/${object}"
}

if [[ "$mode" == "daily" ]]; then
  month_existing=""
  if compgen -G "${LOCAL_ROOT}/monthly/svoi-pravila-${month}"*.dump.age >/dev/null; then
    month_existing=1
  fi
  if [[ -z "$month_existing" ]]; then
    cp -p "$final_path" "${LOCAL_ROOT}/monthly/${name}"
    chmod 0600 "${LOCAL_ROOT}/monthly/${name}"
    if [[ "$(id -u)" -eq 0 ]]; then
      chown --reference="$LOCAL_ROOT" "${LOCAL_ROOT}/monthly/${name}" 2>/dev/null || true
    fi
    log "kept ${name} as the monthly backup for ${month}"
  fi
  prune_local daily "$KEEP_DAILY"
  prune_local monthly "$KEEP_MONTHLY"
else
  prune_local pre-release "$KEEP_PRE_RELEASE"
fi

if [[ "$offsite_enabled" -eq 1 ]]; then
  copy_offsite "$dir" "$name"
  if [[ "$mode" == "daily" ]]; then
    if [[ -f "${LOCAL_ROOT}/monthly/${name}" ]]; then
      month_remote="$(rclone lsf --files-only --include "svoi-pravila-${month}*.dump.age" \
        "sp:${remote_path}/monthly" 2>/dev/null || true)"
      if [[ -z "$month_remote" ]]; then
        copy_offsite monthly "$name"
      fi
    fi
    prune_remote daily "$KEEP_DAILY"
    prune_remote monthly "$KEEP_MONTHLY"
  else
    prune_remote pre-release "$KEEP_PRE_RELEASE"
  fi
fi

log "done"
