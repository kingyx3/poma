#!/usr/bin/env python3
"""Deliver a bounded batch of due Telegram notifications from the shared state outbox."""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from alert_diagnostics import send_message  # noqa: E402
from poma.telegram_outbox import drain  # noqa: E402


def main() -> int:
    path = Path(os.getenv("POMA_CRON_ALERT_STATE_DIR", "state"))
    delivered, attempted = drain(path, lambda message: send_message(message, queue_failure=False))
    if attempted:
        print(f"Telegram outbox: delivered={delivered}, attempted={attempted}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
