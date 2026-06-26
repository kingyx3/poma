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

    TARGET.write_text("\n".join(helper) + "\n", encoding="utf-8")
    os.chown(TARGET, 0, 0)
    TARGET.chmod(stat.S_IRWXU | stat.S_IXGRP | stat.S_IRGRP)
    print(f"Installed {TARGET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
