#!/usr/bin/env bash
set -euxo pipefail

APP_USER="${app_user}"
APP_DIR="${app_dir}"
IB_GATEWAY_DIR="/opt/ibgateway"
IB_GATEWAY_INSTALLER_URL="https://download2.interactivebrokers.com/installers/ibgateway/stable-standalone/ibgateway-stable-standalone-linux-x64.sh"

export DEBIAN_FRONTEND=noninteractive

apt-get update
apt-get install -y \
  ca-certificates \
  cron \
  curl \
  fluxbox \
  git \
  netcat-openbsd \
  openjdk-17-jre-headless \
  procps \
  x11vnc \
  xvfb

if ! command -v docker >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com | sh
fi

if ! id "$${APP_USER}" >/dev/null 2>&1; then
  useradd --create-home --shell /bin/bash "$${APP_USER}"
fi

usermod -aG docker "$${APP_USER}"
mkdir -p "$${APP_DIR}" "$${APP_DIR}/reports" "$${APP_DIR}/state" "$${APP_DIR}/logs"
mkdir -p /home/"$${APP_USER}"/Jts
chown -R "$${APP_USER}:$${APP_USER}" "$${APP_DIR}" /home/"$${APP_USER}"/Jts

if [ ! -x "$${IB_GATEWAY_DIR}/ibgateway" ]; then
  tmp_installer="$(mktemp /tmp/ibgateway-installer.XXXXXX.sh)"
  curl -fsSL "$${IB_GATEWAY_INSTALLER_URL}" -o "$${tmp_installer}"
  chmod +x "$${tmp_installer}"
  bash "$${tmp_installer}" -q -dir "$${IB_GATEWAY_DIR}"
  rm -f "$${tmp_installer}"
fi
chown -R "$${APP_USER}:$${APP_USER}" "$${IB_GATEWAY_DIR}"

cat >/usr/local/bin/poma-run-ib-gateway <<'SCRIPT'
#!/usr/bin/env bash
set -euo pipefail

export HOME="/home/poma"
export DISPLAY="${DISPLAY:-:99}"
export IB_GATEWAY_DIR="${IB_GATEWAY_DIR:-/opt/ibgateway}"
export IB_GATEWAY_VNC_PORT="${IB_GATEWAY_VNC_PORT:-5900}"

mkdir -p "${HOME}/Jts" /tmp/poma-ibgateway

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
systemctl enable --now docker
systemctl enable --now cron
systemctl enable --now ibgateway
