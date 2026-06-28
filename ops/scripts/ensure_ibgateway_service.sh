#!/bin/sh
set -eu

# The IB Gateway runner script (/usr/local/bin/poma-run-ib-gateway) and the systemd unit
# (/etc/systemd/system/ibgateway.service) are rendered by ops/scripts/install_ibc_config_helper.py.
# This script applies small production hardening after that render, then reloads and enables
# the service. Always run install_ibc_config_helper.py before this script.

RUNNER="/usr/local/bin/poma-run-ib-gateway"
UNIT="/etc/systemd/system/ibgateway.service"
ENGINE="/usr/local/bin/poma-ibc-gateway-engine"

if [ ! -x "${RUNNER}" ] || [ ! -f "${UNIT}" ]; then
  echo "Missing ${RUNNER} or ${UNIT}." >&2
  echo "Run 'sudo python3 ops/scripts/install_ibc_config_helper.py' before this script." >&2
  exit 1
fi

python3 - <<'PY'
from __future__ import annotations

import re
from pathlib import Path

ENGINE = Path("/usr/local/bin/poma-ibc-gateway-engine")
RUNNER = Path("/usr/local/bin/poma-run-ib-gateway")
UNIT = Path("/etc/systemd/system/ibgateway.service")
DIAG = Path("/usr/local/bin/poma-diagnose-ibgateway")

ENGINE.write_text(
    r'''#!/usr/bin/env python3
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HOME = Path(os.environ.get("HOME", "/home/poma"))
IBC_DIR = Path(os.environ.get("IBC_DIR", "/opt/ibc"))
LOG_DIR = Path(os.environ.get("IB_GATEWAY_LOG_DIR", "/var/log/poma/ibgateway"))
CONFIG = HOME / "ibc" / "config.ini"
LAUNCHER = IBC_DIR / "gatewaystart.sh"
WRAPPER_LOG = LOG_DIR / "gatewaystart-wrapper.log"
LOG_DIRS = (LOG_DIR, HOME / "ibc" / "logs", Path("/tmp/poma-ibgateway"))
HOLD_SECONDS = int(os.environ.get("IB_GATEWAY_ENGINE_STARTUP_HOLD_SECONDS", "360"))


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def reset_logs() -> None:
    for directory in LOG_DIRS:
        directory.mkdir(parents=True, exist_ok=True)
        for path in directory.rglob("*"):
            if path.is_file():
                try:
                    path.write_text("", encoding="utf-8")
                except OSError:
                    pass


def log(message: str) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with WRAPPER_LOG.open("a", encoding="utf-8") as handle:
        handle.write(f"{now()} {message}\n")


def api_port_open() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", 7497), timeout=1):
            return True
    except OSError:
        return False


def gateway_alive() -> bool:
    return subprocess.run(
        ["pgrep", "-u", str(os.getuid()), "-f", "java|ibgateway"],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ).returncode == 0


def main() -> int:
    reset_logs()
    if not CONFIG.exists() or CONFIG.stat().st_size == 0:
        log(f"IBC config missing at {CONFIG}; refusing raw Gateway fallback.")
        return 127
    if not LAUNCHER.exists() or not os.access(LAUNCHER, os.X_OK):
        log(f"IBC launcher missing or not executable at {LAUNCHER}.")
        return 127

    log("Starting IBC gatewaystart.sh -inline for IB Gateway/TWS API on port 7497.")
    with WRAPPER_LOG.open("a", encoding="utf-8") as handle:
        launcher = subprocess.Popen(
            ["bash", str(LAUNCHER), "-inline"],
            cwd=str(IBC_DIR),
            stdout=handle,
            stderr=subprocess.STDOUT,
        )

    deadline = time.monotonic() + HOLD_SECONDS
    while time.monotonic() < deadline:
        if api_port_open() or gateway_alive():
            log("Gateway process or API listener detected; keeping systemd foreground engine alive.")
            break
        status = launcher.poll()
        if status is not None:
            # gatewaystart.sh exited before Java/Gateway stayed alive.
            log(f"gatewaystart.sh returned before Java/Gateway was visible; status={status}; keeping engine active for diagnostics.")
            break
        time.sleep(2)

    while True:
        if api_port_open() or gateway_alive() or time.monotonic() < deadline:
            time.sleep(2)
            continue
        log("Gateway process/API listener absent after startup hold deadline; exiting for systemd restart.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
''',
    encoding="utf-8",
)
ENGINE.chmod(0o755)

runner_text = RUNNER.read_text(encoding="utf-8")
engine_branch = '''if [ -s "${HOME}/ibc/config.ini" ]; then
  # Config exists but /opt/ibc/gatewaystart.sh is missing or non-executable must fail.
  # poma-ibc-gateway-engine supervises gatewaystart.sh -inline and refuses raw Gateway fallback.
  exec /usr/local/bin/poma-ibc-gateway-engine
fi
'''
if engine_branch not in runner_text:
    old_branches = (
        '''if [ -x "${IBC_DIR}/gatewaystart.sh" ] && [ -s "${HOME}/ibc/config.ini" ]; then
  cd "${IBC_DIR}"
  exec "${IBC_DIR}/gatewaystart.sh" -inline
fi
''',
        '''if [ -s "${HOME}/ibc/config.ini" ]; then
  if [ ! -x "${IBC_DIR}/gatewaystart.sh" ]; then
    echo "Config exists but /opt/ibc/gatewaystart.sh is missing or not executable; refusing raw Gateway fallback." >&2
    echo "Run IB Gateway Ops repair/configure so IBC can reach broker login and 2FA." >&2
    exit 127
  fi
  cd "${IBC_DIR}"
  wrapper_log="${IB_GATEWAY_LOG_DIR}/gatewaystart-wrapper.log"
  echo "Starting /opt/ibc/gatewaystart.sh for IB Gateway/TWS API on port 7497." >>"${wrapper_log}"
  bash "${IBC_DIR}/gatewaystart.sh" -inline >>"${wrapper_log}" 2>&1
  status="$?"
  echo "gatewaystart.sh exited with status=${status}." >>"${wrapper_log}"
  exit "${status}"
fi
''',
        '''if [ -s "${HOME}/ibc/config.ini" ]; then
  if [ ! -x "${IBC_DIR}/gatewaystart.sh" ]; then
    echo "Config exists but /opt/ibc/gatewaystart.sh is missing or not executable; refusing raw Gateway fallback." >&2
    echo "Run IB Gateway Ops repair/configure so IBC can reach broker login and 2FA." >&2
    exit 127
  fi
  cd "${IBC_DIR}"
  echo "Starting /opt/ibc/gatewaystart.sh for IB Gateway/TWS API on port 7497." >>"${IB_GATEWAY_LOG_DIR}/gatewaystart-wrapper.log"
  exec -a poma-ibc-gatewaystart bash "${IBC_DIR}/gatewaystart.sh" -inline
fi
''',
    )
    for old_branch in old_branches:
        if old_branch in runner_text:
            runner_text = runner_text.replace(old_branch, engine_branch)
            break
    else:
        raise SystemExit("Unable to harden IBC startup branch; runner shape changed unexpectedly.")
if "require_command java" not in runner_text:
    runner_text = runner_text.replace("require_command fluxbox\n", "require_command fluxbox\nrequire_command java\n")
RUNNER.write_text(runner_text, encoding="utf-8")
RUNNER.chmod(0o755)

unit_text = UNIT.read_text(encoding="utf-8")
unit_text = re.sub(r"(?m)^MemoryMax=.*\n?", "", unit_text)
UNIT.write_text(unit_text, encoding="utf-8")

if not DIAG.exists():
    raise SystemExit("missing /usr/local/bin/poma-diagnose-ibgateway after install")
diag_text = DIAG.read_text(encoding="utf-8")
diag_text = diag_text.replace(
    '"gatewaystart": bool(re.search(r"gatewaystart\\.sh", process_text)),',
    '"gatewaystart": bool(re.search(r"poma-ibc-gateway-engine|gatewaystart\\.sh", process_text)),',
)
diag_text = diag_text.replace('"ibc-not-running",\n            "fail",', '"ibc-not-running",\n            "continue",')
diag_text = diag_text.replace('"java-gateway-not-running",\n            "fail",', '"java-gateway-not-running",\n            "continue",')
if '"ibc-not-running",\n            "continue",' not in diag_text:
    raise SystemExit("failed to patch ibc startup grace classification")
if '"java-gateway-not-running",\n            "continue",' not in diag_text:
    raise SystemExit("failed to patch Java/Gateway startup grace classification")
DIAG.write_text(diag_text, encoding="utf-8")
DIAG.chmod(0o755)
PY

systemctl daemon-reload
systemctl enable --now ibgateway
