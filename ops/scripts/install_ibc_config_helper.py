#!/usr/bin/env python3
# ruff: noqa: E501
from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

HELPER_TARGET = Path("/usr/local/bin/poma-configure-ibc")
RUNNER_TARGET = Path("/usr/local/bin/poma-run-ib-gateway")
SERVICE_TARGET = Path("/etc/systemd/system/ibgateway.service")
APP_USER = "poma"
IB_GATEWAY_DIR = Path("/opt/ibgateway")
IBC_DIR = Path("/opt/ibc")
IB_GATEWAY_RUNTIME_DIR = Path("/run/poma-ibgateway")
IB_GATEWAY_LOG_DIR = Path("/var/log/poma/ibgateway")
LEGACY_RUNTIME_DIR = Path("/tmp/poma-ibgateway")
DEFAULT_IB_GATEWAY_MAJOR_VERSION = "1019"

APT_PACKAGES = (
    "ca-certificates",
    "curl",
    "fluxbox",
    "netcat-openbsd",
    "openjdk-17-jre-headless",
    "procps",
    "unzip",
    "x11vnc",
    "xterm",
    "xvfb",
)
REQUIRED_COMMAND_PACKAGES = {
    "Xvfb": "xvfb",
    "fluxbox": "fluxbox",
    "java": "openjdk-17-jre-headless",
    "nc": "netcat-openbsd",
    "x11vnc": "x11vnc",
}

CONFIG_HELPER_TEXT = r'''#!/usr/bin/env bash
set -euo pipefail

IBC_DIR="/opt/ibc"
IBC_HOME="/home/poma/ibc"
IBC_CONFIG="${IBC_HOME}/config.ini"

read -r -p "IBKR login id: " ib_login_id
read -r -s -p "IBKR password: " ib_password
echo
read -r -p "Trading mode [paper/live] (default paper): " trading_mode
trading_mode="${trading_mode:-paper}"

if [ "${trading_mode}" != "paper" ] && [ "${trading_mode}" != "live" ]; then
  echo "Trading mode must be paper or live" >&2
  exit 1
fi

install -d -m 700 -o poma -g poma "${IBC_HOME}" "${IBC_HOME}/logs"
if [ ! -f "${IBC_CONFIG}" ]; then
  if [ -f "${IBC_DIR}/config.ini" ]; then
    install -m 600 -o poma -g poma "${IBC_DIR}/config.ini" "${IBC_CONFIG}"
  else
    : > "${IBC_CONFIG}"
    chown poma:poma "${IBC_CONFIG}"
    chmod 600 "${IBC_CONFIG}"
  fi
fi

set_ini() {
  local key="$1"
  local value="$2"
  local tmp
  tmp="$(mktemp)"
  awk -v key="${key}" -v value="${value}" '
    BEGIN { done = 0 }
    index($0, key "=") == 1 { print key "=" value; done = 1; next }
    { print }
    END { if (!done) print key "=" value }
  ' "${IBC_CONFIG}" > "${tmp}"
  cat "${tmp}" > "${IBC_CONFIG}"
  rm -f "${tmp}"
}

set_ini IbLoginId "${ib_login_id}"
set_ini IbPassword "${ib_password}"
set_ini TradingMode "${trading_mode}"
set_ini ReloginAfterSecondFactorAuthenticationTimeout yes
set_ini AcceptNonBrokerageAccountWarning yes
set_ini ExistingSessionDetectedAction primaryoverride
set_ini AutoRestartTime 23:45

chown poma:poma "${IBC_CONFIG}"
chmod 600 "${IBC_CONFIG}"
systemctl restart ibgateway

echo "IBC config written to ${IBC_CONFIG} and ibgateway restarted."
echo "Approve IBKR Mobile 2FA if prompted."
'''

RUNNER_TEXT = r'''#!/usr/bin/env bash
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
"""


def ensure_runtime_packages() -> None:
    missing_packages = sorted(
        {
            package
            for command, package in REQUIRED_COMMAND_PACKAGES.items()
            if shutil.which(command) is None
        }
    )
    if not missing_packages:
        return

    print(
        "Installing missing IB Gateway runtime packages: "
        + ", ".join(missing_packages)
    )
    subprocess.run(["apt-get", "update"], check=True)
    subprocess.run(["apt-get", "install", "-y", *APT_PACKAGES], check=True)


def install_text(path: Path, text: str, mode: int) -> None:
    path.write_text(text, encoding="utf-8")
    os.chown(path, 0, 0)
    path.chmod(mode)


def ensure_app_user() -> None:
    if subprocess.run(["id", APP_USER], check=False, stdout=subprocess.DEVNULL).returncode == 0:
        return
    subprocess.run(["useradd", "--create-home", "--shell", "/bin/bash", APP_USER], check=True)


def find_gateway_jars_dirs() -> list[Path]:
    return sorted(
        path
        for path in IB_GATEWAY_DIR.glob("**/jars")
        if path.is_dir() and any(path.glob("*.jar"))
    )


def find_numeric_ancestor(path: Path) -> Path | None:
    for ancestor in (path.parent, *path.parents):
        if ancestor.name.isdigit():
            return ancestor
    return None


def current_gatewaystart_version(text: str) -> str | None:
    match = re.search(r"(?m)^TWS_MAJOR_VRSN=([0-9]+)\s*$", text)
    return match.group(1) if match else None


def gateway_version_from_jars_dir(jars_dir: Path, gatewaystart_text: str) -> str:
    version_dir = find_numeric_ancestor(jars_dir)
    if version_dir is not None:
        return version_dir.name

    existing_version = current_gatewaystart_version(gatewaystart_text)
    if existing_version is not None:
        return existing_version

    print(
        "Unable to infer numeric IB Gateway version from "
        f"{jars_dir}; falling back to {DEFAULT_IB_GATEWAY_MAJOR_VERSION}."
    )
    return DEFAULT_IB_GATEWAY_MAJOR_VERSION


def gateway_tws_path_from_jars_dir(jars_dir: Path) -> Path:
    version_dir = find_numeric_ancestor(jars_dir)
    if version_dir is not None:
        return version_dir.parent
    return IB_GATEWAY_DIR


def configure_ibc_launcher() -> None:
    gatewaystart = IBC_DIR / "gatewaystart.sh"
    if not gatewaystart.exists():
        print(f"Skipping IBC launcher repair because {gatewaystart} is missing")
        return

    jars_dirs = find_gateway_jars_dirs()
    if not jars_dirs:
        print(f"Skipping IBC launcher repair because no Gateway jars exist under {IB_GATEWAY_DIR}")
        return
    gateway_jars_dir = jars_dirs[-1]
    text = gatewaystart.read_text(encoding="utf-8")
    gateway_major_version = gateway_version_from_jars_dir(gateway_jars_dir, text)
    gateway_tws_path = gateway_tws_path_from_jars_dir(gateway_jars_dir)
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
    for key, value in replacements.items():
        pattern = rf"(?<![A-Za-z0-9_]){re.escape(key)}=[^\s\r\n]*"
        replacement = f"{key}={value}"
        text, count = re.subn(pattern, replacement, text, count=1)
        if count == 0:
            text = f"{replacement}\n{text}"
    gatewaystart.write_text(text, encoding="utf-8")


def main() -> int:
    ensure_runtime_packages()
    ensure_app_user()
    for path, mode in (
        (Path("/home/poma/Jts"), 0o700),
        (Path("/home/poma/ibc/logs"), 0o700),
        (IB_GATEWAY_RUNTIME_DIR, 0o700),
        (IB_GATEWAY_LOG_DIR, 0o750),
        (LEGACY_RUNTIME_DIR, 0o700),
    ):
        path.mkdir(parents=True, exist_ok=True)
        path.chmod(mode)
    subprocess.run(
        [
            "chown",
            "-R",
            "poma:poma",
            "/home/poma/Jts",
            "/home/poma/ibc",
            str(IB_GATEWAY_RUNTIME_DIR),
            str(IB_GATEWAY_LOG_DIR),
            str(LEGACY_RUNTIME_DIR),
        ],
        check=True,
    )

    configure_ibc_launcher()
    install_text(HELPER_TARGET, CONFIG_HELPER_TEXT, stat.S_IRWXU | stat.S_IXGRP | stat.S_IRGRP)
    install_text(RUNNER_TARGET, RUNNER_TEXT, 0o755)
    install_text(SERVICE_TARGET, SERVICE_TEXT, 0o644)
    subprocess.run(["systemctl", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "enable", "ibgateway"], check=True)
    print(f"Installed {HELPER_TARGET}, {RUNNER_TARGET}, and {SERVICE_TARGET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
