#!/usr/bin/env bash
set -euo pipefail

# Minimal Ubuntu bootstrap for a single-host POMA deployment.
# Run as root or via sudo on a fresh VPS.

APP_USER="${APP_USER:-poma}"
APP_DIR="${APP_DIR:-/opt/poma}"

apt-get update
apt-get install -y ca-certificates curl git cron

if ! command -v docker >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com | sh
fi

if ! id "${APP_USER}" >/dev/null 2>&1; then
  useradd --create-home --shell /bin/bash "${APP_USER}"
fi

usermod -aG docker "${APP_USER}"
mkdir -p "${APP_DIR}" "${APP_DIR}/reports" "${APP_DIR}/state" "${APP_DIR}/logs"
chown -R "${APP_USER}:${APP_USER}" "${APP_DIR}"

echo "Bootstrap complete. Clone the repo into ${APP_DIR}, create .env, then install ops/cron/poma.cron."
