#!/bin/sh
set -eu

# The IB Gateway runner script (/usr/local/bin/poma-run-ib-gateway) and the systemd unit
# (/etc/systemd/system/ibgateway.service) have a single source of truth:
# ops/scripts/install_ibc_config_helper.py. This script only ensures the already-installed
# service is reloaded, enabled, and running, so the deploy and ops flows have one idempotent
# "ensure running" step. Always run install_ibc_config_helper.py before this script.

RUNNER="/usr/local/bin/poma-run-ib-gateway"
UNIT="/etc/systemd/system/ibgateway.service"

if [ ! -x "${RUNNER}" ] || [ ! -f "${UNIT}" ]; then
  echo "Missing ${RUNNER} or ${UNIT}." >&2
  echo "Run 'sudo python3 ops/scripts/install_ibc_config_helper.py' before this script." >&2
  exit 1
fi

systemctl daemon-reload
systemctl enable --now ibgateway
