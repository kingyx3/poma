#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/poma}"

cd "${APP_DIR}"

export POMA_UID="${POMA_UID:-$(id -u)}"
export POMA_GID="${POMA_GID:-$(id -g)}"

mkdir -p reports state logs
chmod u+rwX reports state logs

for dir in reports state logs; do
  if [ ! -w "${dir}" ]; then
    echo "${APP_DIR}/${dir} is not writable by uid ${POMA_UID}." >&2
    echo "Fix ownership before deploying: sudo chown -R ${POMA_UID}:${POMA_GID} ${APP_DIR}/${dir}" >&2
    exit 1
  fi
done

docker compose build \
  --build-arg "APP_UID=${POMA_UID}" \
  --build-arg "APP_GID=${POMA_GID}"

# The dry-run smoke test is the slowest part on the free-tier VM; dev-on-PR deploys skip it
# (RUN_DEPLOY_SMOKE=false) for fast feedback, while stg/prd keep it as a deploy gate.
if [ "${RUN_DEPLOY_SMOKE:-true}" = "false" ]; then
  echo "Skipping dry-run deploy smoke test (RUN_DEPLOY_SMOKE=false)."
else
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
fi

echo "Deploy complete. Install cron for scheduled checks; keep IB Gateway supervised separately."
