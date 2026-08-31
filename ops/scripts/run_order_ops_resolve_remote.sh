#!/usr/bin/env bash
set -euo pipefail

resolver_path="${1:?resolver path required}"
order_ref="${2:?order ref required}"
perm_id="${3:?perm id required}"
expected_current_quantity="${4:?expected current quantity required}"
confirmation="${5:?confirmation required}"
actor="${6:-unknown}"
workflow_run_id="${7:-unknown}"

if [ ! -f /opt/poma/docker-compose.vm.yml ]; then
  echo 'POMA app not deployed at /opt/poma (missing docker-compose.vm.yml)' >&2
  exit 1
fi
if [ ! -r "${resolver_path}" ]; then
  echo "Resolver is not readable: ${resolver_path}" >&2
  exit 1
fi

echo '===== guarded unresolved-order resolution ====='
echo 'This action changes only the local durable order ledger after evidence checks.'
echo 'It does not submit/cancel/replace broker orders and does not clear rebalance session state.'
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
    --order-ref "${order_ref}" \
    --perm-id "${perm_id}" \
    --expected-current-quantity "${expected_current_quantity}" \
    --confirmation "${confirmation}" \
    --actor "${actor}" \
    --workflow-run-id "${workflow_run_id}" \
    --apply
