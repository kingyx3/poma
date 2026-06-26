#!/bin/sh
set -eu

APP_USER="poma"

if ! id "${APP_USER}" >/dev/null 2>&1; then
  useradd --create-home --shell /bin/bash "${APP_USER}"
fi

mkdir -p /home/poma/Jts /home/poma/ibc/logs /tmp/poma-ibgateway /var/log/poma/ibgateway
chown -R poma:poma /home/poma/Jts /home/poma/ibc /tmp/poma-ibgateway /var/log/poma/ibgateway
chmod 700 /tmp/poma-ibgateway
chmod 750 /var/log/poma/ibgateway

cat >/usr/local/bin/poma-run-ib-gateway <<'SCRIPT'
#!/usr/bin/env bash
set -euo pipefail

export HOME="/home/poma"
export DISPLAY="${DISPLAY:-:99}"
export IB_GATEWAY_DIR="${IB_GATEWAY_DIR:-/opt/ibgateway}"
export IBC_DIR="${IBC_DIR:-/opt/ibc}"
export IB_GATEWAY_VNC_PORT="${IB_GATEWAY_VNC_PORT:-5900}"
export TWS_SETTINGS_PATH="${TWS_SETTINGS_PATH:-/home/poma/Jts}"
export IB_GATEWAY_RUNTIME_DIR="${IB_GATEWAY_RUNTIME_DIR:-/run/poma-ibgateway}"
export IB_GATEWAY_LOG_DIR="${IB_GATEWAY_LOG_DIR:-/var/log/poma/ibgateway}"

mkdir -p "${HOME}/Jts" "${HOME}/ibc/logs" "${IB_GATEWAY_RUNTIME_DIR}" "${IB_GATEWAY_LOG_DIR}"

require_command() {
  local command="$1"
  if ! command -v "${command}" >/dev/null 2>&1; then
    echo "Missing required command: ${command}. Run IB Gateway Ops to repair the VM bootstrap." >&2
    exit 127
  fi
}

cleanup() {
  jobs -p | xargs -r kill || true
}
trap cleanup EXIT

require_command Xvfb
require_command fluxbox
require_command x11vnc

Xvfb "${DISPLAY}" -screen 0 1280x1024x24 -nolisten tcp >"${IB_GATEWAY_LOG_DIR}/xvfb.log" 2>&1 &
sleep 2
fluxbox >"${IB_GATEWAY_LOG_DIR}/fluxbox.log" 2>&1 &
x11vnc \
  -display "${DISPLAY}" \
  -localhost \
  -forever \
  -shared \
  -nopw \
  -rfbport "${IB_GATEWAY_VNC_PORT}" \
  >"${IB_GATEWAY_LOG_DIR}/x11vnc.log" 2>&1 &

if [ -x "${IBC_DIR}/gatewaystart.sh" ] && [ -s "${HOME}/ibc/config.ini" ]; then
  cd "${IBC_DIR}"
  exec "${IBC_DIR}/gatewaystart.sh" -inline
fi

gateway_executable="$(find "${IB_GATEWAY_DIR}" -type f -name ibgateway -perm -111 2>/dev/null | sort -V | tail -n1 || true)"
if [ -z "${gateway_executable}" ]; then
  echo "Unable to find an executable IB Gateway binary under ${IB_GATEWAY_DIR}." >&2
  echo "Run IB Gateway Ops to repair the VM bootstrap and install IB Gateway." >&2
  exit 127
fi

exec "${gateway_executable}"
SCRIPT
chmod 0755 /usr/local/bin/poma-run-ib-gateway

cat >/etc/systemd/system/ibgateway.service <<'UNIT'
[Unit]
Description=Interactive Brokers Gateway for POMA
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=poma
Group=poma
Environment=HOME=/home/poma
Environment=DISPLAY=:99
Environment=IB_GATEWAY_DIR=/opt/ibgateway
Environment=IBC_DIR=/opt/ibc
Environment=IB_GATEWAY_VNC_PORT=5900
Environment=IB_GATEWAY_RUNTIME_DIR=/run/poma-ibgateway
Environment=IB_GATEWAY_LOG_DIR=/var/log/poma/ibgateway
RuntimeDirectory=poma-ibgateway
RuntimeDirectoryMode=0700
LogsDirectory=poma/ibgateway
LogsDirectoryMode=0750
ExecStart=/usr/local/bin/poma-run-ib-gateway
Restart=always
RestartSec=30
TimeoutStartSec=120
MemoryMax=850M

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable ibgateway
