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

engine = Path("/usr/local/bin/poma-ibc-gateway-engine")
engine.write_text(
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


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


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


def gateway_process_alive() -> bool:
    result = subprocess.run(
        ["pgrep", "-u", str(os.getuid()), "-f", "java|ibgateway"],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return result.returncode == 0


def main() -> int:
    if not CONFIG.exists() or CONFIG.stat().st_size == 0:
        log(f"IBC config missing at {CONFIG}; refusing raw Gateway fallback.")
        return 127
    if not LAUNCHER.exists() or not os.access(LAUNCHER, os.X_OK):
        log(f"IBC launcher missing or not executable at {LAUNCHER}.")
        return 127

    log("Starting IBC gatewaystart.sh -inline for IB Gateway/TWS API on port 7497.")
    with WRAPPER_LOG.open("a", encoding="utf-8") as handle:
        process = subprocess.Popen(
            ["bash", str(LAUNCHER), "-inline"],
            cwd=str(IBC_DIR),
            stdout=handle,
            stderr=subprocess.STDOUT,
            start_new_session=False,
        )

    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        if api_port_open() or gateway_process_alive():
            log("Gateway process or API listener detected; keeping systemd foreground engine alive.")
            break
        status = process.poll()
        if status is not None:
            log(f"gatewaystart.sh exited before Java/Gateway stayed alive; status={status}.")
            return status or 1
        time.sleep(2)
    else:
        status = process.poll()
        log(f"gatewaystart.sh did not produce a Java/Gateway process within 90s; launcher_status={status}.")
        return status or 1

    while True:
        if process.poll() is not None and not api_port_open() and not gateway_process_alive():
            log(f"gatewaystart.sh exited and no Java/Gateway process remains; status={process.returncode}.")
            return process.returncode or 1
        if not api_port_open() and not gateway_process_alive():
            log("Gateway process/API listener disappeared; exiting for systemd restart.")
            return 1
        time.sleep(10)


if __name__ == "__main__":
    sys.exit(main())
''',
    encoding="utf-8",
)
engine.chmod(0o755)

runner = Path("/usr/local/bin/poma-run-ib-gateway")
text = runner.read_text(encoding="utf-8")
old_inline = '''if [ -x "${IBC_DIR}/gatewaystart.sh" ] && [ -s "${HOME}/ibc/config.ini" ]; then
  cd "${IBC_DIR}"
  exec "${IBC_DIR}/gatewaystart.sh" -inline
fi
'''
old_logged = '''if [ -s "${HOME}/ibc/config.ini" ]; then
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
'''
old_marked = '''if [ -s "${HOME}/ibc/config.ini" ]; then
  if [ ! -x "${IBC_DIR}/gatewaystart.sh" ]; then
    echo "Config exists but /opt/ibc/gatewaystart.sh is missing or not executable; refusing raw Gateway fallback." >&2
    echo "Run IB Gateway Ops repair/configure so IBC can reach broker login and 2FA." >&2
    exit 127
  fi
  cd "${IBC_DIR}"
  echo "Starting /opt/ibc/gatewaystart.sh for IB Gateway/TWS API on port 7497." >>"${IB_GATEWAY_LOG_DIR}/gatewaystart-wrapper.log"
  exec -a poma-ibc-gatewaystart bash "${IBC_DIR}/gatewaystart.sh" -inline
fi
'''
new = '''if [ -s "${HOME}/ibc/config.ini" ]; then
  # Config exists but /opt/ibc/gatewaystart.sh is missing or non-executable must fail.
  # poma-ibc-gateway-engine supervises gatewaystart.sh -inline and refuses raw Gateway fallback.
  exec /usr/local/bin/poma-ibc-gateway-engine
fi
'''
for candidate in (old_inline, old_logged, old_marked):
    if candidate in text:
        text = text.replace(candidate, new)
        break
else:
    if new not in text:
        raise SystemExit("Unable to harden IBC startup branch; runner shape changed unexpectedly.")
if "require_command java" not in text:
    text = text.replace("require_command fluxbox\n", "require_command fluxbox\nrequire_command java\n")
runner.write_text(text, encoding="utf-8")
runner.chmod(0o755)

unit = Path("/etc/systemd/system/ibgateway.service")
unit_text = unit.read_text(encoding="utf-8")
unit_text = re.sub(r"(?m)^MemoryMax=.*\n?", "", unit_text)
unit.write_text(unit_text, encoding="utf-8")
PY

python3 - <<'PY'
from pathlib import Path

helper = Path("/usr/local/bin/poma-diagnose-ibgateway")
if not helper.exists():
    raise SystemExit("missing /usr/local/bin/poma-diagnose-ibgateway after install")

text = helper.read_text(encoding="utf-8")
text = text.replace(
    '"gatewaystart": bool(re.search(r"gatewaystart\\.sh", process_text)),',
    '"gatewaystart": bool(re.search(r"poma-ibc-gateway-engine|gatewaystart\\.sh", process_text)),',
)
text = text.replace(
'''    if config_exists and not has_gatewaystart:
        return StartupClassification(
            "ibc-not-running",
            "fail",
            "IBC config exists but gatewaystart.sh is not running; Gateway likely never reached login.",
        )
    if not (has_java or has_ibgateway):
        return StartupClassification(
            "java-gateway-not-running",
            "fail",
            "No Java/IB Gateway process is running, so no IBKR mobile notification can be sent.",
        )
''',
'''    if config_exists and not has_gatewaystart:
        return StartupClassification(
            "ibc-not-running",
            "continue",
            "IBC/Gateway engine is still within startup grace; launcher may not be visible yet.",
        )
    if not (has_java or has_ibgateway):
        return StartupClassification(
            "java-gateway-not-running",
            "continue",
            "IBC is starting but Java/IB Gateway has not stayed alive yet.",
        )
''',
)
text = text.replace(
'''    if (
        classification.stage == "gateway-running-no-login-progress"
        and elapsed_seconds >= fail_no_progress_after
    ):
        classification = StartupClassification(
            "gateway-running-no-login-progress-timeout",
            "fail",
            "IBC/Gateway stayed alive but did not show login, 2FA, or API progress before the "
            f"{fail_no_progress_after}s startup-progress deadline.",
        )
''',
'''    if classification.stage in {
        "ibc-not-running",
        "java-gateway-not-running",
        "gateway-running-no-login-progress",
        "gateway-starting",
    } and elapsed_seconds >= fail_no_progress_after:
        classification = StartupClassification(
            f"{classification.stage}-timeout",
            "fail",
            "IBC/Gateway did not reach login, 2FA, or API readiness before the "
            f"{fail_no_progress_after}s startup-progress deadline.",
        )
''',
)
if '"ibc-not-running",\n            "continue",' not in text:
    raise SystemExit("failed to patch ibc-not-running startup grace classification")
if '"java-gateway-not-running",\n            "continue",' not in text:
    raise SystemExit("failed to patch java-gateway-not-running startup grace classification")
helper.write_text(text, encoding="utf-8")
helper.chmod(0o755)
PY

systemctl daemon-reload
systemctl enable --now ibgateway
