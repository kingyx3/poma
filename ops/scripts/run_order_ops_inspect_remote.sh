#!/usr/bin/env bash
set -euo pipefail

inspector_path="${1:-/tmp/poma-inspect-unresolved-orders.py}"

if [ ! -f /opt/poma/docker-compose.vm.yml ]; then
  echo 'POMA app not deployed at /opt/poma (missing docker-compose.vm.yml)' >&2
  exit 1
fi
if [ ! -r "${inspector_path}" ]; then
  echo "Inspector is not readable: ${inspector_path}" >&2
  exit 1
fi

echo '===== unresolved order inspection ====='
echo 'This action is read-only: it does not reconcile, cancel, replace, or mutate order state.'
echo 'Waiting for the shared POMA command lock so this IBKR read cannot collide with monitor/reconcile.'

cd /opt/poma
exec 9>/opt/poma/state/poma-command.lock
if ! flock -w 90 9; then
  echo 'Unable to acquire /opt/poma/state/poma-command.lock within 90 seconds' >&2
  exit 75
fi

exec docker compose --env-file .compose.env -f docker-compose.vm.yml run --rm \
  --entrypoint python \
  -v "${inspector_path}:${inspector_path}:ro" \
  -v /opt/poma/logs:/host-logs:ro \
  poma "${inspector_path}"
