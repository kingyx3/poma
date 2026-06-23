#!/usr/bin/env bash
set -euxo pipefail

APP_USER="${app_user}"
APP_DIR="${app_dir}"
IB_GATEWAY_DIR="/opt/ibgateway"
IB_GATEWAY_INSTALLER_URL="https://download2.interactivebrokers.com/installers/ibgateway/stable-standalone/ibgateway-stable-standalone-linux-x64.sh"
IBC_VERSION="3.24.0"
IBC_DIR="/opt/ibc"
IBC_ZIP_URL="https://github.com/IbcAlpha/IBC/releases/download/$${IBC_VERSION}/IBCLinux-$${IBC_VERSION}.zip"

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
  unzip \
  x11vnc \
  xterm \
  xvfb

if ! command -v docker >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com | sh
fi

if ! id "$${APP_USER}" >/dev/null 2>&1; then
  useradd --create-home --shell /bin/bash "$${APP_USER}"
fi

usermod -aG docker "$${APP_USER}"
mkdir -p "$${APP_DIR}" "$${APP_DIR}/reports" "$${APP_DIR}/state" "$${APP_DIR}/logs"
mkdir -p /home/"$${APP_USER}"/Jts /home/"$${APP_USER}"/ibc
chown -R "$${APP_USER}:$${APP_USER}" "$${APP_DIR}" /home/"$${APP_USER}"/Jts /home/"$${APP_USER}"/ibc

if [ ! -x "$${IB_GATEWAY_DIR}/ibgateway" ]; then
  tmp_installer="$$(mktemp /tmp/ibgateway-installer.XXXXXX.sh)"
  curl -fsSL "$${IB_GATEWAY_INSTALLER_URL}" -o "$${tmp_installer}"
  chmod +x "$${tmp_installer}"
  bash "$${tmp_installer}" -q -dir "$${IB_GATEWAY_DIR}"
  rm -f "$${tmp_installer}"
fi
chown -R "$${APP_USER}:$${APP_USER}" "$${IB_GATEWAY_DIR}"

if [ ! -x "$${IBC_DIR}/gatewaystart.sh" ]; then
  rm -rf "$${IBC_DIR}"
  mkdir -p "$${IBC_DIR}"
  tmp_ibc_zip="$$(mktemp /tmp/ibc.XXXXXX.zip)"
  curl -fsSL "$${IBC_ZIP_URL}" -o "$${tmp_ibc_zip}"
  unzip -q "$${tmp_ibc_zip}" -d "$${IBC_DIR}"
  rm -f "$${tmp_ibc_zip}"
  chmod +x "$${IBC_DIR}"/*.sh "$${IBC_DIR}"/scripts/*.sh
fi
chown -R "$${APP_USER}:$${APP_USER}" "$${IBC_DIR}"

cat >/usr/local/bin/poma-configure-ibc <<'SCRIPT'
#!/usr/bin/env bash
set -euo pipefail

IBC_DIR="/opt/ibc"
IBC_HOME="/home/poma/ibc"
IBC_CONFIG="$${IBC_HOME}/config.ini"

if [ ! -f "$${IBC_DIR}/config.ini" ]; then
  echo "Missing IBC sample config at $${IBC_DIR}/config.ini" >&2
  exit 1
fi

read -r -p "IBKR login id: " ib_login_id
read -r -s -p "IBKR password: " ib_password
echo
read -r -p "Trading mode [paper/live] (default paper): " trading_mode
trading_mode="$${trading_mode:-paper}"

if [ "$${trading_mode}" != "paper" ] && [ "$${trading_mode}" != "live" ]; then
  echo "Trading mode must be paper or live" >&2
  exit 1
fi

install -d -m 700 -o poma -g poma "$${IBC_HOME}"
if [ ! -f "$${IBC_CONFIG}" ]; then
  install -m 600 -o poma -g poma "$${IBC_DIR}/config.ini" "$${IBC_CONFIG}"
fi

set_ini() {
  local key="$1"
  local value="$2"
  if grep -q "^$${key}=" "$${IBC_CONFIG}"; then
    sed -i "s|^$${key}=.*|$${key}=$${value}|" "$${IBC_CONFIG}"
  else
    printf '%s=%s\n' "$${key}" "$${value}" >> "$${IBC_CONFIG}"
  fi
}

set_ini IbLoginId "$${ib_login_id}"
set_ini IbPassword "$${ib_password}"
set_ini TradingMode "$${trading_mode}"
set_ini ReloginAfterSecondFactorAuthenticationTimeout yes
set_ini AcceptNonBrokerageAccountWarning yes
set_ini ExistingSessionDetectedAction primaryoverride
set_ini AutoRestartTime 23:45

chown poma:poma "$${IBC_CONFIG}"
chmod 600 "$${IBC_CONFIG}"
systemctl restart ibgateway

echo "IBC config written to $${IBC_CONFIG} and ibgateway restarted."
echo "Approve IBKR Mobile 2FA if prompted."
SCRIPT
chmod 0750 /usr/local/bin/poma-configure-ibc

cat >/usr/local/bin/poma-run-ib-gateway <<'SCRIPT'
#!/usr/bin/env bash
set -euo pipefail

export HOME="/home/poma"
export DISPLAY="$${DISPLAY:-:99}"
export IB_GATEWAY_DIR="$${IB_GATEWAY_DIR:-/opt/ibgateway}"
export IBC_DIR="$${IBC_DIR:-/opt/ibc}"
export IB_GATEWAY_VNC_PORT="$${IB_GATEWAY_VNC_PORT:-5900}"
export TWS_SETTINGS_PATH="$${TWS_SETTINGS_PATH:-/home/poma/Jts}"

mkdir -p "$${HOME}/Jts" "$${HOME}/ibc" /tmp/poma-ibgateway

cleanup() {
  jobs -p | xargs -r kill || true
}
trap cleanup EXIT

Xvfb "$${DISPLAY}" -screen 0 1280x1024x24 -nolisten tcp >/tmp/poma-ibgateway/xvfb.log 2>&1 &
sleep 2
fluxbox >/tmp/poma-ibgateway/fluxbox.log 2>&1 &
x11vnc \
  -display "$${DISPLAY}" \
  -localhost \
  -forever \
  -shared \
  -nopw \
  -rfbport "$${IB_GATEWAY_VNC_PORT}" \
  >/tmp/poma-ibgateway/x11vnc.log 2>&1 &

if [ -x "$${IBC_DIR}/gatewaystart.sh" ] && [ -s "$${HOME}/ibc/config.ini" ]; then
  cd "$${IBC_DIR}"
  exec "$${IBC_DIR}/gatewaystart.sh"
fi

exec "$${IB_GATEWAY_DIR}/ibgateway"
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
systemctl enable --now docker
systemctl enable --now cron
systemctl enable --now ibgateway
