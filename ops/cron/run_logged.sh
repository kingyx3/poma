#!/usr/bin/env bash
set -uo pipefail

if [ "$#" -lt 2 ]; then
  echo "usage: $0 LOG_PATH COMMAND [ARG...]" >&2
  exit 64
fi

log_path="$1"
shift
command_text="$*"
lock_path="${POMA_COMMAND_LOCK_PATH:-/opt/poma/state/poma-command.lock}"

utc_timestamp() {
  date -u +"%Y-%m-%dT%H:%M:%SZ"
}

append_timestamped_output() {
  while IFS= read -r line; do
    printf '[%s] %s\n' "$(utc_timestamp)" "${line}"
  done >>"${log_path}"
}

# The alert script reads only this invocation's log slice, not an older failure.
# Keep alert delivery best-effort and outside the command's exit status.
report_command_result() {
  local status="$1" start_offset="$2" command_name="$3"
  python3 ops/cron/alert_diagnostics.py \
    "${log_path}" "${status}" "${command_name}" "${start_offset}" || true
}

mkdir -p "$(dirname "${log_path}")" "$(dirname "${lock_path}")"

# Serialize every scheduled command that can start a POMA container. On the single-core VM,
# overlapping monitor/reconcile containers can starve IB Gateway badly enough for all API calls
# to time out. Do not queue cron invocations behind a long-running job: a skipped reconcile will
# run again on the next reconcile slot, and a skipped monitor will run again on the next 5-minute
# tick.
exec 9>"${lock_path}"
if ! flock -n 9; then
  printf '[%s] ===== command skipped: lock busy (%s): %s =====\n' \
    "$(utc_timestamp)" "${lock_path}" "${command_text}" >>"${log_path}"
  exit 0
fi

# A prior failure requires more than a successful socket call. The app's
# --verify-recovery check audits broker account/positions and recent order history
# before the reporter may mark that incident recovered. Malformed incident state
# triggers verification too (fail closed).
recovery_check_required() {
  python3 - <<'PY'
import json
import os
import sys
from pathlib import Path

path = Path(os.getenv("POMA_CRON_ALERT_STATE_DIR", "state")) / "cron-alert-reconcile-orders.json"
try:
    value = json.loads(path.read_text())
except FileNotFoundError:
    sys.exit(1)
except (OSError, ValueError):
    sys.exit(0)
sys.exit(1 if isinstance(value, dict) and value.get("active") is False else 0)
PY
}

run_start_offset=$(stat -c %s -- "${log_path}" 2>/dev/null || echo 0)
printf '[%s] ===== command start: %s =====\n' "$(utc_timestamp)" "${command_text}" >>"${log_path}"
command_name="${!#}"

set +e
if [[ "${command_name}" == "reconcile-orders" ]] && recovery_check_required; then
  printf '[%s] ===== active broker incident: verifying account, orders and recent executions =====\n' "$(utc_timestamp)" >>"${log_path}"
  "$@" --verify-recovery 2>&1 | append_timestamped_output
  status="${PIPESTATUS[0]}"
else
  "$@" 2>&1 | append_timestamped_output
  status="${PIPESTATUS[0]}"
fi
set -e

# A concurrent *direct* CLI command can hold the in-container runtime lock
# despite the host cron lock. An intentional skip is neither success nor failure.
# Do not close a real broker incident with this spurious zero-work invocation.
if [[ "${command_name}" == "reconcile-orders" && "${status}" -eq 75 ]] \
    && tail -c +"$((run_start_offset + 1))" "${log_path}" | grep -q 'Skipping: another POMA command'; then
  printf '[%s] ===== reconciliation skipped: runtime lock busy =====\n' "$(utc_timestamp)" >>"${log_path}"
  exit 0
fi

printf '[%s] ===== command end: status=%s =====\n' "$(utc_timestamp)" "${status}" >>"${log_path}"
report_command_result "${status}" "${run_start_offset}" "${command_name}"
# Recover earlier failed application and host alerts. This is best-effort and
# separately bounded, so Telegram outages cannot change the trading result.
python3 ops/cron/drain_notifications.py || true
exit "${status}"
