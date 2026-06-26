#!/usr/bin/env bash
set -euo pipefail

APP_USER="poma"

if ! id "${APP_USER}" >/dev/null 2>&1; then
  useradd --create-home --shell /bin/bash "${APP_USER}"
fi

mkdir -p /home/poma/Jts /home/poma/ibc/logs /tmp/poma-ibgateway
chown -R poma:poma /home/poma/Jts /home/poma/ibc

cat >/usr/local/bin/poma-run-ib-gateway <<'SCRIPT'
#!/usr/bin/env bash
set -euo pipefail

export HOME="/home/poma"
export DISPLAY="${DISPLAY:-:99}"
export IB_GATEWAY_DIR="${IB_GATEWAY_DIR:-/opt/ibgateway}"
export IBC_DIR="${IBC_DIR:-/opt/ibc}"
export IB_GATEWAY_VNC_PORT="${IB_GATEWAY_VNC_PORT:-5900}"
export TWS_SETTINGS_PATH="${TWS_SETTINGS_PATH:-/home/poma/Jts}"

mkdir -p "${HOME}/Jts" "${HOME}/ibc/logs" /tmp/poma-ibgateway

cleanup() {
  jobs -p | xargs -r kill || true
}
trap cleanup EXIT

Xvfb "${DISPLAY}" -screen 0 1280x1024x24 -nolisten tcp >/tmp/poma-ibgateway/xvfb.log 2>&1 &
sleep 2
fluxbox >/tmp/poma-ibgateway/fluxbox.log 2>&1 &
x11vnc \
  -display "${DISPLAY}" \
  -localhost \
  -forever \
  -shared \
  -nopw \
  -rfbport "${IB_GATEWAY_VNC_PORT}" \
  >/tmp/poma-ibgateway/x11vnc.log 2>&1 &

if [ -x "${IBC_DIR}/gatewaystart.sh" ] && [ -s "${HOME}/ibc/config.ini" ]; then
  cd "${IBC_DIR}"
  exec "${IBC_DIR}/gatewaystart.sh" -inline
fi

exec "${IB_GATEWAY_DIR}/ibgateway"
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
