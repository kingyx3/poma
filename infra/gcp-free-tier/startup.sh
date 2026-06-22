#!/usr/bin/env bash
set -euxo pipefail

APP_USER="${app_user}"
APP_DIR="${app_dir}"

export DEBIAN_FRONTEND=noninteractive

apt-get update
apt-get install -y ca-certificates curl git cron

if ! command -v docker >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com | sh
fi

if ! id "$${APP_USER}" >/dev/null 2>&1; then
  useradd --create-home --shell /bin/bash "$${APP_USER}"
fi

usermod -aG docker "$${APP_USER}"
mkdir -p "$${APP_DIR}" "$${APP_DIR}/reports" "$${APP_DIR}/state" "$${APP_DIR}/logs"
chown -R "$${APP_USER}:$${APP_USER}" "$${APP_DIR}"

systemctl enable --now docker
systemctl enable --now cron
