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
APP_DIR="${app_dir}"

export DEBIAN_FRONTEND=noninteractive

# The 1 GB e2-micro has no memory headroom for IB Gateway's JVM (~850 MB) plus Docker image builds
# and the pandas app. OOM kills were wedging the VM (dead SSH, not recoverable by a reboot). Add a
# 2 GB swap file so memory pressure pages to disk instead of killing the box.
if ! swapon --show=NAME --noheadings | grep -q '^/swapfile$'; then
  fallocate -l 2G /swapfile || dd if=/dev/zero of=/swapfile bs=1M count=2048
  chmod 600 /swapfile
  mkswap /swapfile
  swapon /swapfile
  grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' >>/etc/fstab
fi

apt-get update
apt-get install -y ca-certificates cron curl python3

if ! command -v docker >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com | sh
fi

if ! id "$${APP_USER}" >/dev/null 2>&1; then
  useradd --create-home --shell /bin/bash "$${APP_USER}"
fi
usermod -aG docker "$${APP_USER}"

mkdir -p \
  "$${APP_DIR}" \
  "$${APP_DIR}/reports" \
  "$${APP_DIR}/state" \
  "$${APP_DIR}/logs" \
  "$${APP_DIR}/data"
chown -R "$${APP_USER}:$${APP_USER}" "$${APP_DIR}"

systemctl enable --now docker
systemctl enable --now cron
