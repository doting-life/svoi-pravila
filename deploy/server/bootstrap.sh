#!/usr/bin/env bash
# One-time (and safely repeatable) preparation of a Debian 13 (trixie) VPS for Svoi Pravila.
#
#   sudo SP_DEPLOY_SSH_PUBKEY='ssh-ed25519 AAAA... you@laptop' \
#        SP_REPO_SSH_URL='git@github.com:<owner>/<repo>.git' \
#        bash bootstrap.sh
#
# Hardens SSH (key-only, deploy user only, no root login), opens ufw 22/80/443, enables
# unattended upgrades, installs Docker from the official Debian apt repository, limits logs,
# sets UTC, prepares /srv/svoi-pravila and /etc/svoi-pravila, clones the repository with a
# read-only deploy key and installs the backup systemd units. Existing configuration files of
# the application are never overwritten.
set -euo pipefail

DEPLOY_USER="deploy"
DEPLOY_UID=1500
DEPLOY_GID=1500
APP_DIR="/srv/svoi-pravila"
ETC_DIR="/etc/svoi-pravila"
BACKUP_REMOTE_DIR="${ETC_DIR}/backup-remote"
REPO_DIR="${APP_DIR}/repo"
REPO_KEY_NAME="svoi_pravila_repo"
GITHUB_HOST_KEY="github.com ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl"
DOCKER_KEY_FINGERPRINT="9DC858229FC7DD38854AE2D88D81803C0EBFCD88"
SSH_KEY_RE='^(ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp[0-9]+|sk-ssh-ed25519@openssh\.com) [A-Za-z0-9+/=]+( .*)?$'

log() {
  printf 'bootstrap: %s\n' "$*"
}

die() {
  printf 'bootstrap: %s\n' "$*" >&2
  exit 1
}

# install_file DEST MODE OWNER:GROUP < content
# Sets FILE_CHANGED to 1 when the file was written and to 0 when it already had this content.
FILE_CHANGED=0
install_file() {
  local dest="$1" mode="$2" owner="$3" tmp
  tmp="$(mktemp)"
  cat >"$tmp"
  if [[ -f "$dest" ]] && cmp -s "$tmp" "$dest"; then
    FILE_CHANGED=0
  else
    install -m "$mode" -o "${owner%%:*}" -g "${owner##*:}" "$tmp" "$dest"
    FILE_CHANGED=1
  fi
  rm -f "$tmp"
}

preflight() {
  [[ "$(id -u)" -eq 0 ]] || die "run as root (sudo)"
  local os_id os_version
  os_id="$(awk -F= '$1 == "ID" { gsub(/"/, "", $2); print $2 }' /etc/os-release)"
  os_version="$(awk -F= '$1 == "VERSION_ID" { gsub(/"/, "", $2); print $2 }' /etc/os-release)"
  [[ "$os_id" == "debian" && "$os_version" == "13" ]] \
    || die "Debian 13 is required, found ${os_id} ${os_version}"
  [[ -n "${SP_DEPLOY_SSH_PUBKEY:-}" ]] || die "SP_DEPLOY_SSH_PUBKEY is required"
  [[ "$SP_DEPLOY_SSH_PUBKEY" =~ $SSH_KEY_RE ]] || die "SP_DEPLOY_SSH_PUBKEY is not a public SSH key"
  if [[ ! -d "${REPO_DIR}/.git" ]]; then
    [[ -n "${SP_REPO_SSH_URL:-}" ]] || die "SP_REPO_SSH_URL is required for the first run"
  fi
}

install_packages() {
  log "installing base packages"
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y -qq --no-install-recommends \
    ca-certificates curl gnupg ufw unattended-upgrades jq git openssh-server openssl sudo
}

setup_deploy_user() {
  local existing_uid existing_gid
  if getent group "$DEPLOY_USER" >/dev/null; then
    existing_gid="$(getent group "$DEPLOY_USER" | cut -d: -f3)"
    [[ "$existing_gid" == "$DEPLOY_GID" ]] \
      || die "group ${DEPLOY_USER} exists with gid ${existing_gid}, expected ${DEPLOY_GID}"
  else
    groupadd --gid "$DEPLOY_GID" "$DEPLOY_USER"
  fi
  if id "$DEPLOY_USER" >/dev/null 2>&1; then
    existing_uid="$(id -u "$DEPLOY_USER")"
    existing_gid="$(id -g "$DEPLOY_USER")"
    [[ "$existing_uid" == "$DEPLOY_UID" && "$existing_gid" == "$DEPLOY_GID" ]] \
      || die "user ${DEPLOY_USER} exists with uid:gid ${existing_uid}:${existing_gid}, expected ${DEPLOY_UID}:${DEPLOY_GID}"
  else
    log "creating user ${DEPLOY_USER} (${DEPLOY_UID}:${DEPLOY_GID})"
    useradd --uid "$DEPLOY_UID" --gid "$DEPLOY_GID" --create-home --shell /bin/bash \
      "$DEPLOY_USER"
  fi
  local ssh_dir="/home/${DEPLOY_USER}/.ssh"
  install -d -m 0700 -o "$DEPLOY_USER" -g "$DEPLOY_USER" "$ssh_dir"
  touch "${ssh_dir}/authorized_keys"
  chmod 0600 "${ssh_dir}/authorized_keys"
  chown "${DEPLOY_USER}:${DEPLOY_USER}" "${ssh_dir}/authorized_keys"
  if ! grep -qxF "$SP_DEPLOY_SSH_PUBKEY" "${ssh_dir}/authorized_keys"; then
    printf '%s\n' "$SP_DEPLOY_SSH_PUBKEY" >>"${ssh_dir}/authorized_keys"
    log "authorized the deploy SSH key"
  fi
  usermod -aG sudo "$DEPLOY_USER"
  log "added ${DEPLOY_USER} to the sudo group"
}

ensure_deploy_sudo_password() {
  # passwd -S second field: P = password set, L/NP/LK = locked or empty.
  local status
  status="$(passwd -S "$DEPLOY_USER" | awk '{ print $2 }')"
  if [[ "$status" == "P" ]]; then
    return 0
  fi
  if [[ -t 0 ]]; then
    log "set a password for ${DEPLOY_USER} (sudo only; SSH stays key-only)"
    passwd "$DEPLOY_USER"
  else
    die "deploy has no sudo password; re-run bootstrap with a TTY so passwd can run before sshd hardening"
  fi
  status="$(passwd -S "$DEPLOY_USER" | awk '{ print $2 }')"
  [[ "$status" == "P" ]] || die "deploy sudo password was not set"
}

sshd_effective() {
  local key="$1"
  sshd -T | awk -v k="$key" 'tolower($1) == k { print tolower($2); exit }'
}

list_sshd_conflicts() {
  local pattern="$1"
  local f
  shopt -s nullglob
  for f in /etc/ssh/sshd_config.d/*; do
    if grep -qiE "$pattern" "$f"; then
      printf '%s\n' "$f"
    fi
  done
  shopt -u nullglob
}

assert_sshd_effective() {
  local permitroot passwordauth kbdinteractive allowusers
  local conflicts=()
  permitroot="$(sshd_effective permitrootlogin)"
  passwordauth="$(sshd_effective passwordauthentication)"
  kbdinteractive="$(sshd_effective kbdinteractiveauthentication)"
  allowusers="$(sshd_effective allowusers)"
  if [[ "$permitroot" == "no" \
    && "$passwordauth" == "no" \
    && "$kbdinteractive" == "no" \
    && "$allowusers" == "$DEPLOY_USER" ]]; then
    log "sshd -T ok (permitrootlogin=no passwordauthentication=no kbdinteractiveauthentication=no allowusers=${DEPLOY_USER})"
    return 0
  fi
  mapfile -t conflicts < <(
    {
      list_sshd_conflicts '^[[:space:]]*PermitRootLogin[[:space:]]+'
      list_sshd_conflicts '^[[:space:]]*PasswordAuthentication[[:space:]]+'
      list_sshd_conflicts '^[[:space:]]*KbdInteractiveAuthentication[[:space:]]+'
      list_sshd_conflicts '^[[:space:]]*AllowUsers[[:space:]]+'
    } | sort -u
  )
  die "sshd -T assertion failed: permitrootlogin='${permitroot}' passwordauthentication='${passwordauth}' kbdinteractiveauthentication='${kbdinteractive}' allowusers='${allowusers}'; conflicting drop-ins: ${conflicts[*]:-none}"
}

harden_sshd() {
  install_file /etc/ssh/sshd_config.d/10-svoi-pravila.conf 0644 root:root <<EOF
PermitRootLogin no
PasswordAuthentication no
KbdInteractiveAuthentication no
PubkeyAuthentication yes
X11Forwarding no
AllowUsers ${DEPLOY_USER}
MaxAuthTries 3
LoginGraceTime 30
ClientAliveInterval 300
ClientAliveCountMax 2
EOF
  sshd -t || die "sshd configuration is invalid; check /etc/ssh/sshd_config.d/10-svoi-pravila.conf"
  # Debian 13 may enable ssh.socket; reload-via-HUP then fails to rebind port 22.
  if systemctl is-enabled --quiet ssh.socket || systemctl is-active --quiet ssh.socket; then
    log "disabling ssh.socket; using ssh.service as the OpenSSH listener"
    systemctl disable --now ssh.socket
    systemctl enable --now ssh.service
  fi
  systemctl try-reload-or-restart ssh
  if [[ "$FILE_CHANGED" -eq 1 ]]; then
    log "sshd hardened (keep this session open and test a deploy login before closing it)"
  fi
  assert_sshd_effective
}

setup_firewall() {
  log "configuring ufw (22, 80, 443)"
  ufw default deny incoming >/dev/null
  ufw default allow outgoing >/dev/null
  ufw allow 22/tcp >/dev/null
  ufw allow 80/tcp >/dev/null
  ufw allow 443/tcp >/dev/null
  ufw --force enable >/dev/null
}

setup_unattended_upgrades() {
  # Debian's 50unattended-upgrades already includes Debian-Security origins; keep reboot policy here.
  install_file /etc/apt/apt.conf.d/20auto-upgrades 0644 root:root <<'EOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
EOF
  install_file /etc/apt/apt.conf.d/52svoi-pravila-unattended 0644 root:root <<'EOF'
Unattended-Upgrade::Automatic-Reboot "true";
Unattended-Upgrade::Automatic-Reboot-Time "04:30";
Unattended-Upgrade::Remove-Unused-Dependencies "true";
EOF
}

install_docker() {
  local tmp fingerprint arch
  if [[ ! -s /etc/apt/keyrings/docker.asc ]]; then
    log "fetching the Docker apt key"
    tmp="$(mktemp)"
    curl -fsSL https://download.docker.com/linux/debian/gpg -o "$tmp"
    fingerprint="$(gpg --show-keys --with-colons "$tmp" | awk -F: '$1 == "fpr" { print $10; exit }')"
    [[ "$fingerprint" == "$DOCKER_KEY_FINGERPRINT" ]] \
      || die "Docker apt key fingerprint mismatch: ${fingerprint}"
    install -d -m 0755 /etc/apt/keyrings
    install -m 0644 "$tmp" /etc/apt/keyrings/docker.asc
    rm -f "$tmp"
  fi
  arch="$(dpkg --print-architecture)"
  install_file /etc/apt/sources.list.d/docker.sources 0644 root:root <<EOF
Types: deb
URIs: https://download.docker.com/linux/debian
Suites: trixie
Components: stable
Architectures: ${arch}
Signed-By: /etc/apt/keyrings/docker.asc
EOF
  if [[ "$FILE_CHANGED" -eq 1 ]]; then
    apt-get update -qq
  fi
  log "installing Docker Engine and the compose plugin"
  apt-get install -y -qq --no-install-recommends \
    docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  usermod -aG docker "$DEPLOY_USER"
}

setup_logging_limits() {
  install -d -m 0755 /etc/docker /etc/systemd/journald.conf.d
  install_file /etc/docker/daemon.json 0644 root:root <<'EOF'
{
  "log-driver": "json-file",
  "log-opts": {
    "max-size": "10m",
    "max-file": "5"
  },
  "live-restore": true
}
EOF
  if [[ "$FILE_CHANGED" -eq 1 ]]; then
    systemctl restart docker
    log "docker daemon log rotation applied"
  fi
  install_file /etc/systemd/journald.conf.d/10-svoi-pravila.conf 0644 root:root <<'EOF'
[Journal]
SystemMaxUse=500M
MaxRetentionSec=1month
EOF
  if [[ "$FILE_CHANGED" -eq 1 ]]; then
    systemctl restart systemd-journald
  fi
}

setup_time() {
  timedatectl set-timezone UTC
  if dpkg-query -W -f='${Status}' chrony | grep -q 'install ok installed'; then
    log "chrony is installed; leaving it as the time sync service"
    if systemctl cat chrony.service >/dev/null; then
      systemctl enable --now chrony.service
    elif systemctl cat chronyd.service >/dev/null; then
      systemctl enable --now chronyd.service
    else
      die "chrony is installed but neither chrony.service nor chronyd.service is available"
    fi
  else
    log "installing systemd-timesyncd"
    export DEBIAN_FRONTEND=noninteractive
    apt-get install -y -qq --no-install-recommends systemd-timesyncd
    timedatectl set-ntp true
  fi
}

setup_directories() {
  install -d -m 0750 -o "$DEPLOY_USER" -g "$DEPLOY_USER" \
    "$APP_DIR" "${APP_DIR}/releases" "${APP_DIR}/state"
  install -d -m 0700 -o "$DEPLOY_USER" -g "$DEPLOY_USER" \
    "${APP_DIR}/backups" \
    "${APP_DIR}/backups/daily" \
    "${APP_DIR}/backups/monthly" \
    "${APP_DIR}/backups/pre-release"
  install -d -m 0750 -o root -g "$DEPLOY_USER" "$ETC_DIR"
  install -d -m 0700 -o "$DEPLOY_USER" -g "$DEPLOY_USER" "$BACKUP_REMOTE_DIR"
}

as_deploy() {
  sudo -u "$DEPLOY_USER" -H "$@"
}

setup_repository() {
  local ssh_dir="/home/${DEPLOY_USER}/.ssh"
  local key="${ssh_dir}/${REPO_KEY_NAME}"
  if [[ ! -f "$key" ]]; then
    as_deploy ssh-keygen -q -t ed25519 -N "" -C "svoi-pravila-repo-readonly" -f "$key"
  fi
  install_file "${ssh_dir}/known_hosts" 0644 "${DEPLOY_USER}:${DEPLOY_USER}" <<<"$GITHUB_HOST_KEY"
  install_file "${ssh_dir}/config" 0600 "${DEPLOY_USER}:${DEPLOY_USER}" <<EOF
Host github.com
  IdentityFile ~/.ssh/${REPO_KEY_NAME}
  IdentitiesOnly yes
  StrictHostKeyChecking yes
EOF
  if [[ -d "${REPO_DIR}/.git" ]]; then
    return 0
  fi
  if ! as_deploy git ls-remote "$SP_REPO_SSH_URL" HEAD >/dev/null 2>&1; then
    log "the repository is not readable yet. Add this read-only deploy key on GitHub"
    log "(Settings, Deploy keys, leave 'Allow write access' unchecked), then rerun this script:"
    cat "${key}.pub"
    exit 3
  fi
  as_deploy git clone "$SP_REPO_SSH_URL" "$REPO_DIR"
}

setup_env_files() {
  if [[ ! -f "${ETC_DIR}/env" ]]; then
    install -m 0600 -o "$DEPLOY_USER" -g "$DEPLOY_USER" \
      "${REPO_DIR}/deploy/env.prod.example" "${ETC_DIR}/env"
    log "created ${ETC_DIR}/env from the example; fill every CHANGE_ME value"
  fi
  if [[ ! -f "${ETC_DIR}/ghcr-token" ]]; then
    install -m 0600 -o "$DEPLOY_USER" -g "$DEPLOY_USER" /dev/null "${ETC_DIR}/ghcr-token"
    log "created an empty ${ETC_DIR}/ghcr-token; put a read:packages token in it"
  fi
}

install_systemd_units() {
  local changed=0 unit
  for unit in svoi-pravila-backup.service svoi-pravila-backup.timer; do
    install_file "/etc/systemd/system/${unit}" 0644 root:root \
      <"${REPO_DIR}/deploy/backup/${unit}"
    if [[ "$FILE_CHANGED" -eq 1 ]]; then
      changed=1
    fi
  done
  if [[ "$changed" -eq 1 ]]; then
    systemctl daemon-reload
  fi
  systemctl enable --now svoi-pravila-backup.timer
}

preflight
install_packages
setup_deploy_user
ensure_deploy_sudo_password
harden_sshd
log "keep this session open; from a second terminal test: ssh ${DEPLOY_USER}@<host> && sudo true"
setup_firewall
setup_unattended_upgrades
install_docker
setup_logging_limits
setup_time
setup_directories
setup_repository
setup_env_files
install_systemd_units
log "done. Next: fill ${ETC_DIR}/env and ${ETC_DIR}/ghcr-token, then run deploy/release.sh <git-sha> as ${DEPLOY_USER}"
