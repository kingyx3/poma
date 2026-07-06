#!/usr/bin/env bash
set -euo pipefail

: "${DEPLOY_ENVIRONMENT:?}"
: "${GCP_PROJECT_ID:?}"
: "${GCP_ZONE:?}"
: "${GCP_VM_NAME:?}"

log_lines="${LOG_LINES:-120}"
if ! [[ "${log_lines}" =~ ^[0-9]+$ ]]; then
  echo "::error::LOG_LINES must be a positive integer" >&2
  exit 2
fi

remote_script=$(cat <<'REMOTE'
set -euo pipefail

log_lines="${POMA_RECONCILE_LOG_LINES:-120}"
app_dir="/opt/poma"
open_orders_path="${app_dir}/state/orders/open_orders.jsonl"
manual_log="${app_dir}/logs/poma-reconcile-manual.log"

if [ ! -f "${app_dir}/docker-compose.vm.yml" ]; then
  echo "POMA app not deployed at ${app_dir} (missing docker-compose.vm.yml)" >&2
  exit 1
fi

cd "${app_dir}"
sudo install -d -o poma -g poma "${app_dir}/logs" "${app_dir}/state/orders"

echo "===== open POMA orders before reconcile ====="
if sudo -u poma test -s "${open_orders_path}"; then
  open_count="$(sudo -u poma wc -l < "${open_orders_path}")"
  echo "Open order ledger rows: ${open_count}"
  sudo -u poma tail -n "${log_lines}" "${open_orders_path}"
else
  echo "No open order ledger rows found."
fi

echo "===== poma reconcile-orders ====="
sudo -u poma bash -lc 'cd /opt/poma && set -o pipefail && docker compose --env-file .compose.env -f docker-compose.vm.yml run --rm poma reconcile-orders 2>&1 | tee -a logs/poma-reconcile-manual.log'

echo "===== open POMA orders after reconcile ====="
if sudo -u poma test -s "${open_orders_path}"; then
  open_count="$(sudo -u poma wc -l < "${open_orders_path}")"
  echo "Open order ledger rows: ${open_count}"
  sudo -u poma tail -n "${log_lines}" "${open_orders_path}"
else
  echo "No open order ledger rows found."
fi

echo "===== manual reconcile log tail ====="
sudo -u poma tail -n "${log_lines}" "${manual_log}" 2>/dev/null || echo "No manual reconcile log yet."
REMOTE
)

printf -v quoted_log_lines '%q' "${log_lines}"
remote_command="POMA_RECONCILE_LOG_LINES=${quoted_log_lines} bash -s <<'POMA_REMOTE'
${remote_script}
POMA_REMOTE"

ssh_common=(
  --zone "${GCP_ZONE}"
  --tunnel-through-iap
  --ssh-key-expire-after=15m
  --quiet
  --verbosity=error
  --no-user-output-enabled
)

timeout --kill-after=30s 2m gcloud config set project "${GCP_PROJECT_ID}"
timeout --kill-after=30s 10m gcloud compute ssh "${GCP_VM_NAME}" "${ssh_common[@]}" --command "${remote_command}"
