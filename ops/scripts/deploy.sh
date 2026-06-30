#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/poma}"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.vm.yml}"
COMPOSE_ENV_FILE="${COMPOSE_ENV_FILE:-.compose.env}"

timestamp() {
  date -u +"%Y-%m-%dT%H:%M:%SZ"
}

log() {
  echo "[$(timestamp)] $*"
}

timed() {
  local label="$1" status start
  shift

  start="$(date +%s)"
  log "BEGIN ${label}"
  set +e
  "$@"
  status="$?"
  set -e
  log "END ${label}: $(( $(date +%s) - start ))s (status=${status})"
  return "${status}"
}

compose() {
  docker compose --env-file "${COMPOSE_ENV_FILE}" -f "${COMPOSE_FILE}" "$@"
}

prepare_runtime_dirs() {
  mkdir -p reports state logs data
  chmod u+rwX reports state logs data

  for dir in reports state logs data; do
    if [ ! -w "${dir}" ]; then
      echo "${APP_DIR}/${dir} is not writable by uid ${POMA_UID}." >&2
      echo "Fix ownership before deploying: sudo chown -R ${POMA_UID}:${POMA_GID} ${APP_DIR}/${dir}" >&2
      exit 1
    fi
  done
}

write_compose_env() {
  : "${POMA_IMAGE:?POMA_IMAGE must be set to the prebuilt image tag to deploy}"
  printf 'POMA_IMAGE=%s\n' "${POMA_IMAGE}" >"${COMPOSE_ENV_FILE}"
  chmod 600 "${COMPOSE_ENV_FILE}"
}

pull_image() {
  docker compose version >/dev/null
  log "Docker disk/cache usage before image pull:"
  docker system df || true

  timeout --kill-after=30s 8m compose pull poma

  log "Docker disk/cache usage after image pull:"
  docker system df || true
}

run_deploy_smoke() {
  local after_count before_count

  before_count="$(find reports -maxdepth 1 -type f -name 'rebalance-*.json' | wc -l)"
  timeout --kill-after=30s 3m compose run --rm \
    -e DATA_PROVIDER=fixture \
    -e TRADING_MODE=dry_run \
    poma rebalance --session-date deploy-smoke --dry-run
  after_count="$(find reports -maxdepth 1 -type f -name 'rebalance-*.json' | wc -l)"

  if [ "${after_count}" -le "${before_count}" ]; then
    echo "Deploy smoke test did not create a rebalance report" >&2
    exit 1
  fi
}

prune_dangling_images() {
  # Repeated image deploys can leave dangling layers from older app images.
  # Remove only untagged leftovers so rollback candidates and useful pull cache remain.
  docker image prune -f >/dev/null
}

script_start="$(date +%s)"
cd "${APP_DIR}"

export POMA_UID="${POMA_UID:-$(id -u)}"
export POMA_GID="${POMA_GID:-$(id -g)}"

log "Deploying ${APP_DIR} as uid=${POMA_UID} gid=${POMA_GID}; image=${POMA_IMAGE:-unset}; smoke=${RUN_DEPLOY_SMOKE:-true}"
timed "runtime directory checks" prepare_runtime_dirs
timed "compose image configuration" write_compose_env
timed "docker image pull" pull_image

if [ "${RUN_DEPLOY_SMOKE:-true}" = "false" ]; then
  log "Skipping dry-run deploy smoke test (RUN_DEPLOY_SMOKE=false)."
else
  timed "deploy smoke test" run_deploy_smoke
fi

timed "dangling image prune" prune_dangling_images
log "Deploy complete in $(( $(date +%s) - script_start ))s. Install cron for scheduled checks; keep IB Gateway supervised separately."
