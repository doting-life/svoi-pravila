#!/usr/bin/env bash
# Encrypted PostgreSQL backup: pg_dump -Fc | age → local /backups (atomic).
# Optional offsite copy via rclone remote "sp" when backup-remote/rclone.conf exists.
# Plaintext never touches disk. Runs inside the backup image as UID 1500.
set -euo pipefail
umask 077

KEEP_DAILY=30
KEEP_MONTHLY=12
KEEP_PRE_RELEASE=10
LOCAL_ROOT="${SP_BACKUP_LOCAL_ROOT:-/backups}"
METRICS_DIR="${SP_BACKUP_METRICS_DIR:-/metrics}"
RCLONE_CONF="${RCLONE_CONFIG:-/etc/svoi-pravila/backup-remote/rclone.conf}"
NAME_RE='^svoi-pravila-[0-9]{8}T[0-9]{6}Z(-[0-9a-f]{40})?\.dump\.age$'
AGE_RECIPIENT_RE='^age1[02-9ac-hj-np-z]{58}$'

usage() {
  cat <<'EOF'
Usage: backup.sh [--pre-release]

  (no flag)        daily backup into /backups/daily; the first daily backup of a
                   month is also kept in /backups/monthly. Retention: 30 daily, 12 monthly.
  --pre-release    backup taken right before a release into /backups/pre-release;
                   the release sha is read from SP_RELEASE_SHA when set.

  When /etc/svoi-pravila/backup-remote/rclone.conf defines remote "sp", the same
  object is also copied to sp:${SP_BACKUP_REMOTE_PATH}/<dir>/ with the same
  retention. If the file is absent, a warning is logged and the run succeeds
  (local copy only). A configured offsite that fails makes the run fail
  (local copy is kept).

Required environment: PGHOST PGUSER PGPASSWORD PGDATABASE
                      SP_BACKUP_AGE_RECIPIENT
Optional: SP_BACKUP_REMOTE_PATH (default svoi-pravila), SP_RELEASE_SHA

  After every run (success or failure) the result is written for node-exporter's textfile
  collector to ${SP_BACKUP_METRICS_DIR:-/metrics}/backup_<kind>.prom, kind = daily | pre_release.
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

list_local_dumps() {
  local list_dir="$1"
  local -a files=()
  local f
  # BusyBox find has no -printf; list with bash globs (nullglob).
  shopt -s nullglob
  files=("${list_dir}"/svoi-pravila-*.dump.age)
  shopt -u nullglob
  for f in "${files[@]}"; do
    basename "$f"
  done | sort
}

# Lists object names on a remote prefix. An empty/missing prefix (first offsite run)
# is an expected case and yields no names.
list_remote_dumps() {
  local remote_base="$1"
  local tmp err rc
  tmp="$(mktemp)"
  err="$(mktemp)"
  rc=0
  rclone lsf --files-only "$remote_base" >"$tmp" 2>"$err" || rc=$?
  if [[ "$rc" -eq 0 ]]; then
    sort "$tmp"
    rm -f "$tmp" "$err"
    return 0
  fi
  if grep -qiE 'directory not found|didn.t find section|not found' "$err"; then
    rm -f "$tmp" "$err"
    return 0
  fi
  cat "$err" >&2
  rm -f "$tmp" "$err"
  return "$rc"
}

# Result metrics (ADR-0017 §5). The success timestamp of the previous file is carried over on
# failure, so "last success" never moves backwards or disappears after a bad run.
BACKUP_SIZE=""
KIND=""
METRICS_FILE=""

previous_success_timestamp() {
  [[ -r "$METRICS_FILE" ]] || return 0
  local value
  value="$(sed -n 's/^sp_backup_last_success_timestamp_seconds{kind="[a-z_]*"} \([0-9][0-9]*\)$/\1/p' \
    "$METRICS_FILE" | head -n 1)"
  printf '%s' "$value"
}

write_metrics() {
  local success="$1" now success_ts tmp
  now="$(date -u +%s)"
  if [[ "$success" -eq 1 ]]; then
    success_ts="$now"
  else
    success_ts="$(previous_success_timestamp)"
  fi
  tmp="${METRICS_DIR}/.backup_${KIND}.prom.tmp.$$"
  {
    printf '# HELP sp_backup_last_run_timestamp_seconds Unix time of the last backup run.\n'
    printf '# TYPE sp_backup_last_run_timestamp_seconds gauge\n'
    printf 'sp_backup_last_run_timestamp_seconds{kind="%s"} %s\n' "$KIND" "$now"
    printf '# HELP sp_backup_last_run_success 1 when the last backup run succeeded.\n'
    printf '# TYPE sp_backup_last_run_success gauge\n'
    printf 'sp_backup_last_run_success{kind="%s"} %s\n' "$KIND" "$success"
    if [[ -n "$success_ts" ]]; then
      printf '# HELP sp_backup_last_success_timestamp_seconds Unix time of the last successful backup.\n'
      printf '# TYPE sp_backup_last_success_timestamp_seconds gauge\n'
      printf 'sp_backup_last_success_timestamp_seconds{kind="%s"} %s\n' "$KIND" "$success_ts"
    fi
    if [[ "$success" -eq 1 && -n "$BACKUP_SIZE" ]]; then
      printf '# HELP sp_backup_last_size_bytes Size of the last successful encrypted dump.\n'
      printf '# TYPE sp_backup_last_size_bytes gauge\n'
      printf 'sp_backup_last_size_bytes{kind="%s"} %s\n' "$KIND" "$BACKUP_SIZE"
    fi
  } >"$tmp"
  # umask 077 applies to everything else; node-exporter (another uid) must read this file.
  chmod 0644 "$tmp"
  mv -f "$tmp" "$METRICS_FILE"
}

finish() {
  local rc=$?
  rm -f "${tmp_path:-}"
  local success=0
  if [[ "$rc" -eq 0 ]]; then
    success=1
  fi
  if [[ -d "$METRICS_DIR" ]]; then
    write_metrics "$success" || warn "could not write the result metrics to ${METRICS_DIR}"
  else
    warn "metrics directory ${METRICS_DIR} is missing; result not recorded"
  fi
  return "$rc"
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

KIND="${mode//-/_}"
METRICS_FILE="${METRICS_DIR}/backup_${KIND}.prom"
trap finish EXIT

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
# Create subdirs only; do not chmod the bind-mount root or host-owned seed dirs.
# umask 077 makes dirs we create mode 700 and dump files mode 600.
mkdir -p "${LOCAL_ROOT}/daily" "${LOCAL_ROOT}/monthly" "${LOCAL_ROOT}/pre-release"
final_path="${local_dir}/${name}"
tmp_path="${local_dir}/.${name}.tmp.$$"

log "dumping ${PGDATABASE} to ${dir}/${name}"
pg_dump --format=custom --no-password \
  | age --recipient "$SP_BACKUP_AGE_RECIPIENT" \
  >"$tmp_path"
[[ -s "$tmp_path" ]] || die "encrypted dump is empty"
chmod 0600 "$tmp_path"
mv -f "$tmp_path" "$final_path"
chmod 0600 "$final_path"
size="$(stat -c %s "$final_path")"
[[ "$size" =~ ^[0-9]+$ && "$size" -gt 0 ]] || die "local dump is missing or empty"
BACKUP_SIZE="$size"
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
  done < <(list_local_dumps "${LOCAL_ROOT}/${prune_dir}")
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
  done < <(list_remote_dumps "$remote_base")
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
      month_remote="$(list_remote_dumps "sp:${remote_path}/monthly" \
        | grep -E "^svoi-pravila-${month}" || true)"
      # grep exit 1 means no match — expected when this month has no remote copy yet.
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
