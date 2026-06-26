#!/usr/bin/env python3
from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

SOURCE = Path("/opt/poma/infra/gcp-free-tier/startup.sh")
TARGET = Path("/usr/local/bin/poma-configure-ibc")
START = "cat >/usr/local/bin/poma-configure-ibc <<'SCRIPT'"
END = "SCRIPT"


def patch_helper_text(text: str) -> str:
    missing_block = (
        'if [ ! -f "${IBC_DIR}/config.ini" ]; then\n'
        '  echo "Missing IBC sample config at ${IBC_DIR}/config.ini" >&2\n'
        '  exit 1\n'
        'fi\n\n'
    )
    text = text.replace(missing_block, '')
    sample_line = '  install -m 600 -o poma -g poma "${IBC_DIR}/config.ini" "${IBC_CONFIG}"'
    fallback_lines = (
        '  if [ -f "${IBC_DIR}/config.ini" ]; then\n'
        f'{sample_line}\n'
        '  else\n'
        '    : > "${IBC_CONFIG}"\n'
        '  fi'
    )
    return text.replace(sample_line, fallback_lines)


# Installer used by the Gateway ops workflow to repair older VMs.
def main() -> int:
    if not SOURCE.exists():
        print(f"Missing source startup script: {SOURCE}", file=sys.stderr)
        return 1

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
        return 1

    TARGET.write_text(patch_helper_text("\n".join(helper) + "\n"), encoding="utf-8")
    os.chown(TARGET, 0, 0)
    TARGET.chmod(stat.S_IRWXU | stat.S_IXGRP | stat.S_IRGRP)
    print(f"Installed {TARGET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
