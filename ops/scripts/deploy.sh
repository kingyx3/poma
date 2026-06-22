#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/poma}"

cd "${APP_DIR}"
mkdir -p reports state logs
docker compose build
docker compose run --rm poma monitor --dry-run
docker compose up -d

echo "Deploy complete. Confirm dry-run output before switching TRADING_MODE to paper/live."
