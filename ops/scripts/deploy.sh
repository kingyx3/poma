#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/poma}"

cd "${APP_DIR}"
mkdir -p reports state logs

docker compose build

before_count="$(find reports -maxdepth 1 -type f -name 'rebalance-*.json' | wc -l)"
docker compose run --rm \
  -e DATA_PROVIDER=fixture \
  -e TRADING_MODE=dry_run \
  poma rebalance --session-date deploy-smoke --dry-run
after_count="$(find reports -maxdepth 1 -type f -name 'rebalance-*.json' | wc -l)"

if [ "${after_count}" -le "${before_count}" ]; then
  echo "Deploy smoke test did not create a rebalance report" >&2
  exit 1
fi

echo "Deploy complete. Install cron for scheduled checks; keep IB Gateway supervised separately."
