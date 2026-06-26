#!/usr/bin/env python3
# ruff: noqa: E501
from __future__ import annotations

import os
import re
import stat
import subprocess
import sys
from pathlib import Path

SOURCE = Path("/opt/poma/infra/gcp-free-tier/startup.sh")
HELPER_TARGET = Path("/usr/local/bin/poma-configure-ibc")
RUNNER_TARGET = Path("/usr/local/bin/poma-run-ib-gateway")
SERVICE_TARGET = Path("/etc/systemd/system/ibgateway.service")
START = "cat >/usr/local/bin/poma-configure-ibc <<'SCRIPT'"
END = "SCRIPT"
APP_USER = "poma"
IB_GATEWAY_DIR = Path("/opt/ibgateway")
IBC_DIR = Path("/opt/ibc")

RUNNER_TEXT = r'''#!/usr/bin/env bash
set -euo pipefail

export HOME="/home/poma"
export DISPLAY="${DISPLAY:-:99}"
export IB_GATEWAY_DIR="${IB_GATEWAY_DIR:-/opt/ibgateway}"
export IBC_DIR="${IBC_DIR:-/opt/ibc}"
export IB_GATEWAY_VNC_PORT="${IB_GATEWAY_VNC_PORT:-5900}"
export TWS_SETTINGS_PATH="${TWS_SETTINGS_PATH:-/home/poma/Jts}"

mkdir -p "${HOME}/Jts" "${HOME}/ibc/logs" /tmp/poma-ibgateway

require_command() {
  local command="$1"
  if ! command -v "${command}" >/dev/null 2>&1; then
    echo "Missing required command: ${command}. Re-run Deploy GCP e2-micro VM to repair the VM bootstrap." >&2
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

gateway_executable="$(find "${IB_GATEWAY_DIR}" -type f -name ibgateway -perm -111 2>/dev/null | sort -V | tail -n1 || true)"
if [ -z "${gateway_executable}" ]; then
  echo "Unable to find an executable IB Gateway binary under ${IB_GATEWAY_DIR}." >&2
  echo "Re-run Deploy GCP e2-micro VM so the VM startup script installs IB Gateway." >&2
  exit 127
fi

exec "${gateway_executable}"
'''

SERVICE_TEXT = """[Unit]
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
"""


def patch_helper_text(text: str) -> str:
    missing_block = (
        'if [ ! -f "${IBC_DIR}/config.ini" ]; then\n'
        '  echo "Missing IBC sample config at ${IBC_DIR}/config.ini" >&2\n'
        "  exit 1\n"
        "fi\n\n"
    )
    text = text.replace(missing_block, "")
    sample_line = '  install -m 600 -o poma -g poma "${IBC_DIR}/config.ini" "${IBC_CONFIG}"'
    fallback_lines = (
        '  if [ -f "${IBC_DIR}/config.ini" ]; then\n'
        f"{sample_line}\n"
        "  else\n"
        '    : > "${IBC_CONFIG}"\n'
        "  fi"
    )
    return text.replace(sample_line, fallback_lines)


def extract_config_helper() -> str:
    if not SOURCE.exists():
        print(f"Missing source startup script: {SOURCE}", file=sys.stderr)
        raise SystemExit(1)

    lines = SOURCE.read_text(encoding="utf-8").splitlines()
    capture = False
    helper: list[str] = []
    for line in lines:
        if line == START:
            capture = True
            continue
        if capture and line == END:
            break
        if capture:
            helper.append(line.replace("$${", "${"))

    if not helper:
        print(f"Could not find helper block in {SOURCE}", file=sys.stderr)
        raise SystemExit(1)
    return patch_helper_text("\n".join(helper) + "\n")


def install_text(path: Path, text: str, mode: int) -> None:
    path.write_text(text, encoding="utf-8")
    os.chown(path, 0, 0)
    path.chmod(mode)


def ensure_app_user() -> None:
    if subprocess.run(["id", APP_USER], check=False, stdout=subprocess.DEVNULL).returncode == 0:
        return
    subprocess.run(["useradd", "--create-home", "--shell", "/bin/bash", APP_USER], check=True)


def configure_ibc_launcher() -> None:
    gatewaystart = IBC_DIR / "gatewaystart.sh"
    if not gatewaystart.exists():
        print(f"Skipping IBC launcher repair because {gatewaystart} is missing")
        return

    jars_dirs = sorted(IB_GATEWAY_DIR.glob("**/ibgateway/[0-9]*/jars"))
    if not jars_dirs:
        print(f"Skipping IBC launcher repair because no Gateway jars exist under {IB_GATEWAY_DIR}")
        return

    gateway_jars_dir = jars_dirs[-1]
    gateway_major_version = gateway_jars_dir.parent.name
    gateway_tws_path = gateway_jars_dir.parents[2]
    replacements = {
        "TWS_MAJOR_VRSN": gateway_major_version,
        "IBC_INI": "/home/poma/ibc/config.ini",
        "TRADING_MODE": "",
        "TWOFA_TIMEOUT_ACTION": "exit",
        "IBC_PATH": str(IBC_DIR),
        "TWS_PATH": str(gateway_tws_path),
        "TWS_SETTINGS_PATH": "/home/poma/Jts",
        "LOG_PATH": "/home/poma/ibc/logs",
        "TWSUSERID": "",
        "TWSPASSWORD": "",
        "FIXUSERID": "",
        "FIXPASSWORD": "",
        "JAVA_PATH": "",
        "HIDE": "YES",
    }
    text = gatewaystart.read_text(encoding="utf-8")
    for key, value in replacements.items():
        pattern = rf"(?<![A-Za-z0-9_]){re.escape(key)}=[^\s\r\n]*"
        replacement = f"{key}={value}"
        text, count = re.subn(pattern, replacement, text, count=1)
        if count == 0:
            text = f"{replacement}\n{text}"
    gatewaystart.write_text(text, encoding="utf-8")


def main() -> int:
    ensure_app_user()
    Path("/home/poma/Jts").mkdir(parents=True, exist_ok=True)
    Path("/home/poma/ibc/logs").mkdir(parents=True, exist_ok=True)
    Path("/tmp/poma-ibgateway").mkdir(parents=True, exist_ok=True)
    subprocess.run(["chown", "-R", "poma:poma", "/home/poma/Jts", "/home/poma/ibc"], check=True)

    configure_ibc_launcher()
    install_text(HELPER_TARGET, extract_config_helper(), stat.S_IRWXU | stat.S_IXGRP | stat.S_IRGRP)
    install_text(RUNNER_TARGET, RUNNER_TEXT, 0o755)
    install_text(SERVICE_TARGET, SERVICE_TEXT, 0o644)
    subprocess.run(["systemctl", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "enable", "ibgateway"], check=True)
    print(f"Installed {HELPER_TARGET}, {RUNNER_TARGET}, and {SERVICE_TARGET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
