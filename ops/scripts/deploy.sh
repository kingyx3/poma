#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/poma}"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.vm.yml}"
COMPOSE_ENV_FILE="${COMPOSE_ENV_FILE:-.compose.env}"
DEFAULT_IMAGE_REGISTRY="${DEFAULT_IMAGE_REGISTRY:-ghcr.io}"
DEFAULT_IMAGE_REPOSITORY="${DEFAULT_IMAGE_REPOSITORY:-kingyx3/poma}"
DEFAULT_IMAGE_TAG="${DEFAULT_IMAGE_TAG:-main}"
EXPECTED_APP_UID="${EXPECTED_APP_UID:-1000}"
EXPECTED_APP_GID="${EXPECTED_APP_GID:-1000}"

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

timeout_compose() {
  local duration="$1"
  shift

  timeout --kill-after=30s "${duration}" docker compose --env-file "${COMPOSE_ENV_FILE}" -f "${COMPOSE_FILE}" "$@"
}

ensure_runtime_identity() {
  if [ "${POMA_UID}" != "${EXPECTED_APP_UID}" ] || [ "${POMA_GID}" != "${EXPECTED_APP_GID}" ]; then
    echo "Deploy user uid=${POMA_UID} gid=${POMA_GID} must match image app identity ${EXPECTED_APP_UID}:${EXPECTED_APP_GID}." >&2
    echo "Apply Terraform startup changes so the VM recreates the poma user with the expected identity." >&2
    exit 1
  fi
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

resolve_image() {
  if [ -n "${POMA_IMAGE:-}" ]; then
    return 0
  fi
  export POMA_IMAGE="${DEFAULT_IMAGE_REGISTRY}/${DEFAULT_IMAGE_REPOSITORY}:${DEFAULT_IMAGE_TAG}"
}

write_compose_env() {
  resolve_image
  printf 'POMA_IMAGE=%s\n' "${POMA_IMAGE}" >"${COMPOSE_ENV_FILE}"
  chmod 600 "${COMPOSE_ENV_FILE}"
}

ensure_vm_compose_file() {
  if [ -f "${COMPOSE_FILE}" ]; then
    return 0
  fi

  cat >"${COMPOSE_FILE}" <<'COMPOSE'
services:
  poma:
    image: ${POMA_IMAGE}
    env_file: .env
    command: ["monitor"]
    volumes:
      - ./reports:/app/reports
      - ./state:/app/state
      - ./data:/app/data
    network_mode: host
COMPOSE
}

pull_image() {
  docker compose version >/dev/null
  log "Docker disk/cache usage before image pull:"
  docker system df || true

  timeout_compose 8m pull poma

  log "Docker disk/cache usage after image pull:"
  docker system df || true
}

run_deploy_smoke() {
  local after_count before_count

  before_count="$(find reports -maxdepth 1 -type f -name 'rebalance-*.json' | wc -l)"
  timeout_compose 3m run --rm \
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
# Terraform startup and the prebuilt image both pin the app identity to 1000:1000,
# so pulled containers can write the host runtime mounts without rebuilding on the VM.

log "Deploying ${APP_DIR} as uid=${POMA_UID} gid=${POMA_GID}; image=${POMA_IMAGE:-auto}; smoke=${RUN_DEPLOY_SMOKE:-true}"
timed "runtime identity checks" ensure_runtime_identity
timed "runtime directory checks" prepare_runtime_dirs
timed "compose image configuration" write_compose_env
timed "vm compose file" ensure_vm_compose_file
timed "docker image pull" pull_image

if [ "${RUN_DEPLOY_SMOKE:-true}" = "false" ]; then
  log "Skipping dry-run deploy smoke test (RUN_DEPLOY_SMOKE=false)."
else
  timed "deploy smoke test" run_deploy_smoke
fi

timed "dangling image prune" prune_dangling_images
log "Deploy complete in $(( $(date +%s) - script_start ))s. Install cron for scheduled checks; keep IB Gateway supervised separately."
