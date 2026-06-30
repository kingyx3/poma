#!/bin/sh
set -eu

# The IB Gateway runner script, IBC foreground engine, and systemd unit are rendered by
# ops/scripts/install_ibc_config_helper.py. This script intentionally does not patch the
# generated files; it only validates the installed IBC-managed service contract and enables it.
# Always run install_ibc_config_helper.py before this script.

RUNNER="/usr/local/bin/poma-run-ib-gateway"
ENGINE="/usr/local/bin/poma-ibc-gateway-engine"
UNIT="/etc/systemd/system/ibgateway.service"
DIAG="/usr/local/bin/poma-diagnose-ibgateway"

if [ ! -x "${RUNNER}" ] || [ ! -x "${ENGINE}" ] || [ ! -f "${UNIT}" ]; then
  echo "Missing ${RUNNER}, ${ENGINE}, or ${UNIT}." >&2
  echo "Run 'sudo python3 ops/scripts/install_ibc_config_helper.py' before this script." >&2
  exit 1
fi

if ! grep -q 'poma-ibc-gateway-engine' "${RUNNER}"; then
  echo "${RUNNER} is not configured to launch Gateway through the IBC engine." >&2
  exit 1
fi

if ! grep -q 'ExecStart=/usr/local/bin/poma-run-ib-gateway' "${UNIT}"; then
  echo "${UNIT} does not point systemd at ${RUNNER}." >&2
  exit 1
fi

if [ ! -x "${DIAG}" ]; then
  echo "Missing ${DIAG}." >&2
  exit 1
fi

if ! grep -q '_STARTUP_GRACE_STAGES' "${DIAG}" || \
   ! grep -q '_STARTUP_LOG_NOISY_STAGES' "${DIAG}" || \
   ! grep -q 'poma-ibc-gateway-engine' "${DIAG}"; then
  echo "${DIAG} is missing required IBC startup-stage diagnostics." >&2
  exit 1
fi

systemctl daemon-reload
systemctl enable --now ibgateway
