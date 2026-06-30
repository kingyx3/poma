#!/bin/sh
set -eu

# install_ibc_config_helper.py renders the base service and pins IBC gatewaystart.sh.
# This script renders the final IBC-managed systemd foreground runner. Once an
# IBC config exists, Gateway launches through IBC and fails closed if IBC breaks.
# Configured startup is intentionally refusing raw Gateway fallback.
# Startup diagnostics preserve the gatewaystart.sh exited before Java/Gateway stayed alive marker.

RUNNER=/usr/local/bin/poma-run-ib-gateway
ENGINE=/usr/local/bin/poma-ibc-gateway-engine
UNIT=/etc/systemd/system/ibgateway.service
DIAG=/usr/local/bin/poma-diagnose-ibgateway

if [ ! -f ${UNIT} ]; then
  echo Missing ${UNIT}. >&2
  echo Run sudo python3 ops/scripts/install_ibc_config_helper.py before this script. >&2
  exit 1
fi

cat > ${RUNNER} <<'RUNNER'
#!/usr/bin/env bash
set -euo pipefail

export HOME=/home/poma
export DISPLAY=${DISPLAY:-:99}
export IB_GATEWAY_DIR=${IB_GATEWAY_DIR:-/opt/ibgateway}
export IBC_DIR=${IBC_DIR:-/opt/ibc}
export IB_GATEWAY_VNC_PORT=${IB_GATEWAY_VNC_PORT:-5900}
export TWS_SETTINGS_PATH=${TWS_SETTINGS_PATH:-/home/poma/Jts}
export IB_GATEWAY_RUNTIME_DIR=${IB_GATEWAY_RUNTIME_DIR:-/run/poma-ibgateway}
export IB_GATEWAY_LOG_DIR=${IB_GATEWAY_LOG_DIR:-/var/log/poma/ibgateway}

mkdir -p ${HOME}/Jts ${HOME}/ibc/logs ${IB_GATEWAY_RUNTIME_DIR} ${IB_GATEWAY_LOG_DIR}

require_command() {
  local command=$1
  if ! command -v ${command} >/dev/null 2>&1; then
    echo Missing required command: ${command}. Run IB Gateway Ops to repair the VM bootstrap. >&2
    exit 127
  fi
}

cleanup() {
  jobs -p | xargs -r kill || true
}
trap cleanup EXIT

require_command Xvfb
require_command fluxbox
require_command java
require_command x11vnc

Xvfb ${DISPLAY} -screen 0 1280x1024x24 -nolisten tcp >${IB_GATEWAY_LOG_DIR}/xvfb.log 2>&1 &
sleep 2
fluxbox >${IB_GATEWAY_LOG_DIR}/fluxbox.log 2>&1 &
x11vnc -display ${DISPLAY} -localhost -forever -shared -nopw -rfbport ${IB_GATEWAY_VNC_PORT} >${IB_GATEWAY_LOG_DIR}/x11vnc.log 2>&1 &

if [ -s ${HOME}/ibc/config.ini ]; then
  exec /usr/local/bin/poma-ibc-gateway-engine
fi

gateway_executable=$(find ${IB_GATEWAY_DIR} -type f -name ibgateway -perm -111 2>/dev/null | sort -V | tail -n1 || true)
if [ x${gateway_executable} = x ]; then
  echo Unable to find an executable IB Gateway binary under ${IB_GATEWAY_DIR}. >&2
  echo Run IB Gateway Ops to repair the VM bootstrap and install IB Gateway. >&2
  exit 127
fi

echo IBC config is not present yet - starting raw IB Gateway only for first-time bootstrap/recovery. >&2
exec ${gateway_executable}
RUNNER

cat > ${ENGINE} <<'ENGINE'
#!/usr/bin/env bash
set -euo pipefail

HOME=${HOME:-/home/poma}
IBC_DIR=${IBC_DIR:-/opt/ibc}
LOG_DIR=${IB_GATEWAY_LOG_DIR:-/var/log/poma/ibgateway}
CONFIG=${HOME}/ibc/config.ini
LAUNCHER=${IBC_DIR}/gatewaystart.sh
WRAPPER_LOG=${LOG_DIR}/gatewaystart-wrapper.log
HOLD_SECONDS=${IB_GATEWAY_ENGINE_STARTUP_HOLD_SECONDS:-360}
API_PORT=${IB_GATEWAY_API_PORT:-7497}

log() {
  mkdir -p ${LOG_DIR}
  echo $(date -u +%Y-%m-%dT%H:%M:%SZ) $* >>${WRAPPER_LOG}
}

reset_logs() {
  for directory in ${LOG_DIR} ${HOME}/ibc/logs /tmp/poma-ibgateway; do
    mkdir -p ${directory}
    find ${directory} -type f -exec truncate -s 0 {} + 2>/dev/null || true
  done
}

api_port_open() {
  nc -z 127.0.0.1 ${API_PORT} >/dev/null 2>&1
}

gateway_alive() {
  pgrep -u $(id -u) -f 'java|ibgateway' >/dev/null 2>&1
}

reset_logs
if [ ! -s ${CONFIG} ]; then
  log IBC config missing at ${CONFIG} - refusing configured Gateway startup without IBC.
  exit 127
fi
if [ ! -x ${LAUNCHER} ]; then
  log IBC launcher missing or not executable at ${LAUNCHER}.
  exit 127
fi

log Starting IBC gatewaystart.sh -inline for IB Gateway/TWS API on port ${API_PORT}.
(cd ${IBC_DIR} && bash ${LAUNCHER} -inline >>${WRAPPER_LOG} 2>&1) &
launcher_pid=$!
deadline=$((SECONDS + HOLD_SECONDS))

while [ ${SECONDS} -lt ${deadline} ]; do
  if api_port_open || gateway_alive; then
    log Gateway process or API listener detected - keeping systemd foreground engine alive.
    break
  fi
  if ! kill -0 ${launcher_pid} >/dev/null 2>&1; then
    wait ${launcher_pid} || launcher_status=$?
    log gatewaystart.sh returned before Java/Gateway was visible - status=${launcher_status:-0} - keeping engine active for diagnostics.
    break
  fi
  sleep 2
done

while true; do
  if api_port_open || gateway_alive || [ ${SECONDS} -lt ${deadline} ]; then
    sleep 2
    continue
  fi
  log Gateway process/API listener absent after startup hold deadline - exiting for systemd restart.
  exit 1
done
ENGINE

chmod 755 ${RUNNER} ${ENGINE}

if ! grep -q ExecStart=/usr/local/bin/poma-run-ib-gateway ${UNIT}; then
  echo ${UNIT} does not point systemd at ${RUNNER}. >&2
  exit 1
fi

if [ ! -x ${DIAG} ]; then
  echo Missing ${DIAG}. >&2
  exit 1
fi

if ! grep -q _STARTUP_GRACE_STAGES ${DIAG} || ! grep -q _STARTUP_LOG_NOISY_STAGES ${DIAG} || ! grep -q poma-ibc-gateway-engine ${DIAG}; then
  echo ${DIAG} is missing required IBC startup-stage diagnostics. >&2
  exit 1
fi

systemctl daemon-reload
systemctl enable --now ibgateway
