#!/usr/bin/env bash
# Restore the newest (or a named) encrypted backup into a throw-away database and sanity-check it.
# Default source is the local /backups directory. --remote reads from rclone remote "sp".
# The live database is never touched. Plaintext is streamed, never written to disk.
set -euo pipefail

LOCAL_ROOT="${SP_BACKUP_LOCAL_ROOT:-/backups}"
RCLONE_CONF="${RCLONE_CONFIG:-/etc/svoi-pravila/rclone.conf}"
NAME_RE='^svoi-pravila-[0-9]{8}T[0-9]{6}Z(-[0-9a-f]{40})?\.dump\.age$'

usage() {
  cat <<'EOF'
Usage: restore-verify.sh [--remote] [--dir daily|monthly|pre-release] [OBJECT_NAME]

Defaults to the newest object in /backups/daily.
With --remote, reads from sp:${SP_BACKUP_REMOTE_PATH}/<dir>/ (requires rclone.conf).

Required environment: PGHOST PGUSER PGPASSWORD PGDATABASE
                      SP_BACKUP_AGE_IDENTITY_FILE
Optional: SP_BACKUP_REMOTE_PATH (default svoi-pravila)
EOF
}

log() {
  printf 'restore-verify: %s\n' "$*"
}

die() {
  printf 'restore-verify: %s\n' "$*" >&2
  exit 1
}

dir=daily
object=""
use_remote=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --remote)
      use_remote=1
      shift
      ;;
    --dir)
      [[ $# -ge 2 ]] || die "--dir needs a value"
      dir="$2"
      shift 2
      ;;
    -h | --help)
      usage
      exit 0
      ;;
    -*)
      usage >&2
      exit 2
      ;;
    *)
      [[ -z "$object" ]] || die "only one object name is accepted"
      object="$1"
      shift
      ;;
  esac
done

case "$dir" in
  daily | monthly | pre-release) ;;
  *) die "--dir must be daily, monthly or pre-release" ;;
esac

for var in PGHOST PGUSER PGPASSWORD PGDATABASE SP_BACKUP_AGE_IDENTITY_FILE; do
  [[ -n "${!var:-}" ]] || die "${var} is required"
done
[[ -r "$SP_BACKUP_AGE_IDENTITY_FILE" ]] || die "age identity file is not readable"

remote_path="${SP_BACKUP_REMOTE_PATH:-svoi-pravila}"
remote_path="${remote_path#/}"
remote_path="${remote_path%/}"

list_local() {
  local list_dir="$1"
  if [[ ! -d "${LOCAL_ROOT}/${list_dir}" ]]; then
    return 0
  fi
  ls -1 "${LOCAL_ROOT}/${list_dir}" 2>/dev/null | sort || true
}

list_remote() {
  local list_dir="$1"
  export RCLONE_CONFIG="$RCLONE_CONF"
  rclone lsf --files-only "sp:${remote_path}/${list_dir}" 2>/dev/null | sort || true
}

if [[ "$use_remote" -eq 1 ]]; then
  [[ -r "$RCLONE_CONF" ]] || die "rclone.conf is required for --remote"
  grep -qE '^\[sp\][[:space:]]*$' "$RCLONE_CONF" || die "rclone.conf must define remote [sp]"
  export RCLONE_CONFIG="$RCLONE_CONF"
  listing="$(list_remote "$dir")"
else
  listing="$(list_local "$dir")"
fi

if [[ -z "$object" ]]; then
  while IFS= read -r candidate; do
    if [[ "$candidate" =~ $NAME_RE ]]; then
      object="$candidate"
    fi
  done <<<"$listing"
  [[ -n "$object" ]] || die "no backups found in ${dir}"
fi
[[ "$object" =~ $NAME_RE ]] || die "object name does not look like a Svoi Pravila backup"

scratch="restore_verify_$(date -u +%Y%m%d%H%M%S)_$$"
[[ "$scratch" != "$PGDATABASE" ]] || die "scratch database must differ from the live database"

drop_scratch() {
  dropdb --if-exists --force "$scratch" >/dev/null 2>&1 || true
}
trap drop_scratch EXIT

log "restoring ${dir}/${object} into ${scratch}"
createdb "$scratch"
if [[ "$use_remote" -eq 1 ]]; then
  rclone cat "sp:${remote_path}/${dir}/${object}" \
    | age --decrypt --identity "$SP_BACKUP_AGE_IDENTITY_FILE" \
    | pg_restore --dbname="$scratch" --no-owner --no-acl --exit-on-error
else
  [[ -r "${LOCAL_ROOT}/${dir}/${object}" ]] || die "local object ${dir}/${object} is missing"
  age --decrypt --identity "$SP_BACKUP_AGE_IDENTITY_FILE" \
    <"${LOCAL_ROOT}/${dir}/${object}" \
    | pg_restore --dbname="$scratch" --no-owner --no-acl --exit-on-error
fi

revisions="$(psql --dbname="$scratch" --no-psqlrc --tuples-only --no-align \
  --command='SELECT count(*) FROM alembic_version')"
[[ "$revisions" == "1" ]] || die "alembic_version must hold exactly one row, found ${revisions}"

tables="$(psql --dbname="$scratch" --no-psqlrc --tuples-only --no-align \
  --command="SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public'")"
[[ "$tables" =~ ^[0-9]+$ && "$tables" -gt 1 ]] || die "restored database has no application tables"

revision="$(psql --dbname="$scratch" --no-psqlrc --tuples-only --no-align \
  --command='SELECT version_num FROM alembic_version')"
log "ok: ${tables} public tables, alembic revision ${revision}"
