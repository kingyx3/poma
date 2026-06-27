#!/usr/bin/env python3
from __future__ import annotations

import re
from pathlib import Path

RUNNER = Path("/usr/local/bin/poma-run-ib-gateway")
SERVICE = Path("/etc/systemd/system/ibgateway.service")

OLD_BLOCK = '''if [ -x "${IBC_DIR}/gatewaystart.sh" ] && [ -s "${HOME}/ibc/config.ini" ]; then
  cd "${IBC_DIR}"
  exec "${IBC_DIR}/gatewaystart.sh" -inline
fi
'''

NEW_BLOCK = '''if [ -s "${HOME}/ibc/config.ini" ]; then
  if [ ! -x "${IBC_DIR}/gatewaystart.sh" ]; then
    echo "Config exists but /opt/ibc/gatewaystart.sh is missing or not executable; refusing raw Gateway fallback." >&2
    echo "Run IB Gateway Ops repair/configure so IBC can reach broker login and 2FA." >&2
    exit 127
  fi
  cd "${IBC_DIR}"
  exec "${IBC_DIR}/gatewaystart.sh" -inline
fi
'''


def require_runner() -> str:
    if not RUNNER.exists():
        raise FileNotFoundError(f"missing {RUNNER}")
    text = RUNNER.read_text(encoding="utf-8")
    if OLD_BLOCK in text:
        return text.replace(OLD_BLOCK, NEW_BLOCK)
    if NEW_BLOCK in text:
        return text
    raise RuntimeError("Unable to harden IBC launch block; runner shape changed unexpectedly.")


def ensure_runner_requires_java(text: str) -> str:
    if "require_command java" in text:
        return text
    return text.replace("require_command fluxbox\n", "require_command fluxbox\nrequire_command java\n")


def remove_memory_cap() -> None:
    if not SERVICE.exists():
        return
    text = SERVICE.read_text(encoding="utf-8")
    text = re.sub(r"(?m)^MemoryMax=.*\n?", "", text)
    SERVICE.write_text(text, encoding="utf-8")


def main() -> int:
    text = ensure_runner_requires_java(require_runner())
    RUNNER.write_text(text, encoding="utf-8")
    RUNNER.chmod(0o755)
    remove_memory_cap()
    print("IB Gateway runner hardened for IBC-only configured startup.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
