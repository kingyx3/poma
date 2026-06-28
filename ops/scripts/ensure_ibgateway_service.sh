#!/bin/sh
set -eu

# The IB Gateway runner script (/usr/local/bin/poma-run-ib-gateway) and the systemd unit
# (/etc/systemd/system/ibgateway.service) are rendered by ops/scripts/install_ibc_config_helper.py.
# This script applies small production hardening after that render, then reloads and enables
# the service. Always run install_ibc_config_helper.py before this script.

RUNNER="/usr/local/bin/poma-run-ib-gateway"
UNIT="/etc/systemd/system/ibgateway.service"

if [ ! -x "${RUNNER}" ] || [ ! -f "${UNIT}" ]; then
  echo "Missing ${RUNNER} or ${UNIT}." >&2
  echo "Run 'sudo python3 ops/scripts/install_ibc_config_helper.py' before this script." >&2
  exit 1
fi

python3 - <<'PY'
from __future__ import annotations

import re
from pathlib import Path

runner = Path("/usr/local/bin/poma-run-ib-gateway")
text = runner.read_text(encoding="utf-8")
old = '''if [ -x "${IBC_DIR}/gatewaystart.sh" ] && [ -s "${HOME}/ibc/config.ini" ]; then
  cd "${IBC_DIR}"
  exec "${IBC_DIR}/gatewaystart.sh" -inline
fi
'''
new = '''if [ -s "${HOME}/ibc/config.ini" ]; then
  if [ ! -x "${IBC_DIR}/gatewaystart.sh" ]; then
    echo "Config exists but /opt/ibc/gatewaystart.sh is missing or not executable; refusing raw Gateway fallback." >&2
    echo "Run IB Gateway Ops repair/configure so IBC can reach broker login and 2FA." >&2
    exit 127
  fi
  cd "${IBC_DIR}"
  echo "Starting /opt/ibc/gatewaystart.sh for IB Gateway/TWS API on port 7497." >>"${IB_GATEWAY_LOG_DIR}/gatewaystart-wrapper.log"
  exec -a poma-ibc-gatewaystart bash "${IBC_DIR}/gatewaystart.sh" -inline
fi
'''
if old in text:
    text = text.replace(old, new)
elif new not in text:
    raise SystemExit("Unable to harden IBC startup branch; runner shape changed unexpectedly.")
if "require_command java" not in text:
    text = text.replace("require_command fluxbox\n", "require_command fluxbox\nrequire_command java\n")
runner.write_text(text, encoding="utf-8")
runner.chmod(0o755)

unit = Path("/etc/systemd/system/ibgateway.service")
unit_text = unit.read_text(encoding="utf-8")
unit_text = re.sub(r"(?m)^MemoryMax=.*\n?", "", unit_text)
unit.write_text(unit_text, encoding="utf-8")
PY

systemctl daemon-reload
systemctl enable --now ibgateway
