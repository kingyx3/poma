#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/poma}"
COMPOSE_ENV_FILE="${COMPOSE_ENV_FILE:-.compose.env}"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.vm.yml}"
READY_SENTINEL="${READY_SENTINEL:-/var/lib/poma/vm-ready}"
DOCKER_BIN="${DOCKER_BIN:-/usr/bin/docker}"

command_name="${1:-}"
if [ -z "${command_name}" ]; then
  echo "Missing POMA cron command name; expected monitor or reconcile-orders." >&2
  exit 2
fi
shift || true

case "${command_name}" in
  monitor|reconcile-orders)
    ;;
  *)
    echo "Unsupported POMA cron command: ${command_name}" >&2
    exit 2
    ;;
esac

log_skip() {
  echo "Skipping ${command_name}: $*"
}

missing=0
for required_path in "${APP_DIR}/.env" "${APP_DIR}/${COMPOSE_ENV_FILE}" "${APP_DIR}/${COMPOSE_FILE}" "${READY_SENTINEL}"; do
  if [ ! -s "${required_path}" ]; then
    log_skip "Missing runtime dependency ${required_path}."
    missing=1
  fi
done
if [ "${missing}" -ne 0 ]; then
  exit 0
fi

if ! command -v "${DOCKER_BIN}" >/dev/null 2>&1; then
  log_skip "Docker binary ${DOCKER_BIN} is unavailable."
  exit 0
fi
if ! systemctl is-active --quiet docker; then
  log_skip "Docker service is not active."
  exit 0
fi

cd "${APP_DIR}"
exec "${DOCKER_BIN}" compose --env-file "${COMPOSE_ENV_FILE}" -f "${COMPOSE_FILE}" run --rm poma "${command_name}" "$@"
