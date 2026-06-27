#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/poma}"

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

build_image() {
  docker compose version >/dev/null
  log "Docker disk/cache usage before build:"
  docker system df || true

  DOCKER_BUILDKIT=1 COMPOSE_DOCKER_CLI_BUILD=1 docker compose build \
    --progress=plain \
    --build-arg "APP_UID=${POMA_UID}" \
    --build-arg "APP_GID=${POMA_GID}"

  log "Docker disk/cache usage after build:"
  docker system df || true
}

run_deploy_smoke() {
  local after_count before_count

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
}

prune_dangling_images() {
  # Repeated local builds on the small persistent disk can leave dangling layers from older images.
  # Keep the useful build cache, but remove untagged image leftovers after a successful deploy.
  docker image prune -f >/dev/null
}

script_start="$(date +%s)"
cd "${APP_DIR}"

export POMA_UID="${POMA_UID:-$(id -u)}"
export POMA_GID="${POMA_GID:-$(id -g)}"

log "Deploying ${APP_DIR} as uid=${POMA_UID} gid=${POMA_GID}; smoke=${RUN_DEPLOY_SMOKE:-true}"
timed "runtime directory checks" prepare_runtime_dirs
timed "docker build" build_image

if [ "${RUN_DEPLOY_SMOKE:-true}" = "false" ]; then
  log "Skipping dry-run deploy smoke test (RUN_DEPLOY_SMOKE=false)."
else
  timed "deploy smoke test" run_deploy_smoke
fi

timed "dangling image prune" prune_dangling_images
log "Deploy complete in $(( $(date +%s) - script_start ))s. Install cron for scheduled checks; keep IB Gateway supervised separately."
