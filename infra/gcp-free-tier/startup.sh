#!/usr/bin/env bash
# Minimal host bootstrap: install Docker + cron, create the app user, and prepare runtime dirs.
#
# IB Gateway / IBC, their GUI runtime packages, the systemd service, and the IBC launcher are
# intentionally NOT installed here. They are provisioned by the IB Gateway Ops workflow via
# ops/scripts/repair_ib_gateway_runtime.py + install_ibc_config_helper.py (hardened, idempotent),
# which Auto CI/CD runs after every deploy. Keeping boot light means cloud-init finishes in a
# couple of minutes instead of stalling on the multi-minute IB Gateway installer, so the deploy's
# VM-readiness wait no longer times out.
set -euxo pipefail

APP_USER="${app_user}"
APP_UID="${app_uid}"
APP_GID="${app_gid}"
APP_DIR="${app_dir}"
STARTUP_REVISION="${startup_revision}"
READY_DIR="/var/lib/poma"
READY_SENTINEL="$${READY_DIR}/vm-ready"
FAILED_SENTINEL="$${READY_DIR}/vm-startup-failed"

export DEBIAN_FRONTEND=noninteractive

log_phase() {
  echo "POMA STARTUP phase=$${1}"
}

mkdir -p "$${READY_DIR}"
record_startup_failure() {
  status="$${?}"
  if [ "$${status}" -ne 0 ]; then
    printf '%s exit_status=%s\n' "$${STARTUP_REVISION}" "$${status}" >"$${FAILED_SENTINEL}" || true
    chmod 0644 "$${FAILED_SENTINEL}" || true
  fi
}
trap record_startup_failure EXIT

rm -f "$${READY_SENTINEL}" "$${FAILED_SENTINEL}"

# A recreated or reset VM can inherit an already-installed crontab from the persistent boot disk
# before the new app bundle is installed. Stop cron and remove stale schedules first so old
# monitor/reconcile containers cannot compete with apt, Docker startup, or the deploy upload on
# the 1 GB e2-micro.
log_phase "quiesce-cron"
systemctl stop cron || true
crontab -u ubuntu -r || true
if id "$${APP_USER}" >/dev/null 2>&1; then
  crontab -u "$${APP_USER}" -r || true
fi
pkill -f 'docker compose.*poma' || true

# Ubuntu cloud images already reserve uid/gid 1000 for the default ubuntu identity.
# Keep the numeric app identity aligned with the prebuilt container without renaming
# platform users/groups that the guest agent may still expect.
log_phase "user"
if ! getent group "$${APP_USER}" >/dev/null 2>&1; then
  if getent group "$${APP_GID}" >/dev/null 2>&1; then
    groupadd --non-unique --gid "$${APP_GID}" "$${APP_USER}"
  else
    groupadd --gid "$${APP_GID}" "$${APP_USER}"
  fi
fi
if ! id "$${APP_USER}" >/dev/null 2>&1; then
  if getent passwd "$${APP_UID}" >/dev/null 2>&1; then
    useradd --non-unique --uid "$${APP_UID}" --gid "$${APP_GID}" --create-home --shell /bin/bash "$${APP_USER}"
  else
    useradd --uid "$${APP_UID}" --gid "$${APP_GID}" --create-home --shell /bin/bash "$${APP_USER}"
  fi
fi
if [ "$(id -u "$${APP_USER}")" != "$${APP_UID}" ] || [ "$(id -g "$${APP_USER}")" != "$${APP_GID}" ]; then
  echo "$${APP_USER} must use uid=$${APP_UID} gid=$${APP_GID} so pulled containers can write runtime mounts." >&2
  exit 1
fi

# Remove any app-user crontab that may have been created after the earlier pre-user cleanup.
crontab -u "$${APP_USER}" -r || true

log_phase "runtime-dirs"
mkdir -p \
  "$${APP_DIR}" \
  "$${APP_DIR}/reports" \
  "$${APP_DIR}/state" \
  "$${APP_DIR}/logs" \
  "$${APP_DIR}/data"
chown -R "$${APP_USER}:$${APP_USER}" "$${APP_DIR}"

# The 1 GB e2-micro has no memory headroom for IB Gateway's JVM (~850 MB) plus Docker image builds
# and the pandas app. OOM kills were wedging the VM (dead SSH, not recoverable by a reboot). Add a
# 2 GB swap file so memory pressure pages to disk instead of killing the box.
log_phase "swap"
if ! swapon --show=NAME --noheadings | grep -q '^/swapfile$'; then
  fallocate -l 2G /swapfile || dd if=/dev/zero of=/swapfile bs=1M count=2048
  chmod 600 /swapfile
  mkswap /swapfile
  swapon /swapfile
  grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' >>/etc/fstab
fi

log_phase "apt"
if ! command -v curl >/dev/null 2>&1 || ! command -v python3 >/dev/null 2>&1 || \
  ! command -v docker >/dev/null 2>&1 || ! command -v cron >/dev/null 2>&1; then
  log_phase "apt-update"
  apt-get update
  log_phase "apt-install"
  apt-get install -y --no-install-recommends ca-certificates cron curl python3
else
  echo "POMA STARTUP apt packages already present; skipping apt-get update/install."
fi

log_phase "docker"
if ! command -v docker >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com | sh
fi
rm -rf /var/lib/apt/lists/*

log_phase "groups"
usermod -aG docker "$${APP_USER}"
# $${APP_USER} is intentionally created non-unique on the same uid/gid as the cloud image's
# default "ubuntu" account (see the useradd block above). Tools that resolve a uid back to a
# username via getpwuid -- notably cron, for both "crontab -u $${APP_USER}" installation and the
# executing job's session/privilege-dropping -- land on "ubuntu" for that shared uid, not
# $${APP_USER}, so cron jobs installed for $${APP_USER} actually run as ubuntu. Add ubuntu to the
# same groups so behavior doesn't depend on which name a given tool resolves the shared uid to.
if id -u ubuntu >/dev/null 2>&1 && [ "$${APP_USER}" != "ubuntu" ]; then
  usermod -aG docker ubuntu
fi

log_phase "services"
systemctl enable --now docker
# Use enable+restart (not enable --now) for cron: cron may already be running from an earlier
# invocation of this script on an already-booted host (e.g. a manual startup-script re-run), and
# a long-running cron daemon does not pick up "$${APP_USER}" gaining docker group membership until
# its own process is refreshed. --now is a no-op when the unit is already active, so it alone
# would leave a stale, ungrouped cron process behind; restart is idempotent either way.
systemctl enable cron
systemctl restart cron
systemctl is-active --quiet docker
systemctl is-active --quiet cron
log_phase "ready"
printf '%s %s\n' "$${STARTUP_REVISION}" "$(cat /proc/sys/kernel/random/boot_id)" >"$${READY_SENTINEL}"
chmod 0644 "$${READY_SENTINEL}"
