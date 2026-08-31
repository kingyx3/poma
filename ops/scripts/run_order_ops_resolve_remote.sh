#!/usr/bin/env bash
set -euo pipefail

resolver_path="${1:?resolver path required}"
actor="${2:-unknown}"
workflow_run_id="${3:-unknown}"

if [ ! -f /opt/poma/docker-compose.vm.yml ]; then
  echo 'POMA app not deployed at /opt/poma (missing docker-compose.vm.yml)' >&2
  exit 1
fi
if [ ! -r "${resolver_path}" ]; then
  echo "Resolver is not readable: ${resolver_path}" >&2
  exit 1
fi

echo '===== guarded stale-order resolution ====='
echo 'This action auto-selects only when exactly one unresolved order exists and retained evidence is unambiguous.'
echo 'It changes only the local durable order ledger; it does not submit/cancel/replace broker orders.'
echo 'Waiting for the shared POMA command lock.'

cd /opt/poma
exec 9>/opt/poma/state/poma-command.lock
if ! flock -w 90 9; then
  echo 'Unable to acquire /opt/poma/state/poma-command.lock within 90 seconds' >&2
  exit 75
fi

exec docker compose --env-file .compose.env -f docker-compose.vm.yml run --rm \
  --entrypoint python \
  -v "${resolver_path}:${resolver_path}:ro" \
  poma "${resolver_path}" \
    --actor "${actor}" \
    --workflow-run-id "${workflow_run_id}" \
    --apply
