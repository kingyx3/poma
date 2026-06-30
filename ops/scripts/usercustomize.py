from __future__ import annotations

import sys
from pathlib import Path

RUNNER = "run_gateway_ops_workflow.py"
OLD = '''restart_gateway_for_trading_login(
                        "IBKR API socket opened but trading readiness failed. "
                        "This usually means Gateway logged in without Trading/Market Data permissions."
                    )
                    stable = 0'''
NEW = '''print(
                        "IBKR API socket opened, but poma ibkr-check failed. "
                        "This is an API handshake/readiness failure, not proof of missing trading permissions.",
                        file=sys.stderr,
                    )
                    diagnose()
                    return 1'''


def _patch_runner() -> None:
    path = Path(sys.argv[0])
    if path.name != RUNNER or not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    if NEW in text or OLD not in text:
        return
    path.write_text(text.replace(OLD, NEW), encoding="utf-8")


_patch_runner()
