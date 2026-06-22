#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/poma}"

cd "${APP_DIR}"
mkdir -p reports state logs
docker compose build
docker compose run --rm poma monitor --dry-run

echo "Deploy complete. Install cron for scheduled checks; keep IB Gateway supervised separately."
