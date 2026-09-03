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

send_failure_alert() {
  local status="$1"
  python3 - "${log_path}" "${status}" "${command_text}" <<'PY' || true
from __future__ import annotations

import json
import os
import socket
import sys
import urllib.error
import urllib.request
from pathlib import Path

log_path, status, command_text = sys.argv[1:4]
settings: dict[str, str] = {}
env_path = Path(".env")
if env_path.exists():
    for raw_line in env_path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        settings[key.strip()] = value.strip().strip('"').strip("'")

bot_token = os.environ.get("TELEGRAM_BOT_TOKEN") or settings.get("TELEGRAM_BOT_TOKEN")
chat_id = os.environ.get("TELEGRAM_CHAT_ID") or settings.get("TELEGRAM_CHAT_ID")
if not bot_token or not chat_id:
    raise SystemExit(0)

env_name = (os.environ.get("APP_ENV") or settings.get("APP_ENV") or "unknown").upper()
text = "\n".join(
    [
        f"📈 POMA · {env_name}",
        "🚨 Scheduled command failed",
        f"Host: {socket.gethostname()}",
        f"Status: {status}",
        f"Command: {command_text}",
        f"Log: {log_path}",
    ]
)
payload = json.dumps({"chat_id": chat_id, "text": text}).encode()
request = urllib.request.Request(
    f"https://api.telegram.org/bot{bot_token}/sendMessage",
    data=payload,
    headers={"Content-Type": "application/json"},
    method="POST",
)
try:
    with urllib.request.urlopen(request, timeout=15) as response:
        response.read()
except (OSError, urllib.error.URLError):
    pass
PY
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

printf '[%s] ===== command start: %s =====\n' "$(utc_timestamp)" "${command_text}" >>"${log_path}"

set +e
"$@" 2>&1 | append_timestamped_output
status="${PIPESTATUS[0]}"
set -e

printf '[%s] ===== command end: status=%s =====\n' "$(utc_timestamp)" "${status}" >>"${log_path}"
if [ "${status}" -ne 0 ]; then
  send_failure_alert "${status}"
fi
exit "${status}"
