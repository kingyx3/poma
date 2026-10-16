#!/usr/bin/env python3
"""One-shot read-only health proof. No order submissions, reconcile, or restarts."""
from __future__ import annotations

import json
import re
import subprocess
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path("/opt/poma")
RECOVERY_UTC = datetime(2026, 10, 16, 16, 11, tzinfo=UTC)
CODE = """
import json
from poma.config import get_settings
from poma.broker import build_broker
from poma.order_store import OrderStore
from poma.state import LocalState

settings = get_settings()
try:
    open_orders = list(build_broker(settings).fetch_open_order_snapshots())
    ledger_open = list(OrderStore(settings.state_dir).load_open_orders())
    local = LocalState(settings.state_dir)
    from pathlib import Path
    import json as js
    session_data = js.loads(local.path.read_text()) if local.path.exists() else {}
    poma_broker = [x for x in open_orders if (x.order_ref or "").startswith("poma:")]
    print(json.dumps({"broker_order_query": "success",
        "broker_open_count": len(open_orders),
        "broker_poma_open_count": len(poma_broker),
        "ledger_open_count": len(ledger_open),
        "session_status": session_data.get("last_rebalance_status"),
        "session_date": session_data.get("last_rebalance_session"),
        "trading_mode": settings.trading_mode.value}))
except Exception as exc:
    print(json.dumps({"broker_order_query": "failed", "error_type": type(exc).__name__}))
    raise SystemExit(4)
"""


def service(name: str) -> bool:
    p = subprocess.run(["systemctl", "is-active", "--quiet", name], timeout=6, check=False)
    return p.returncode == 0


def cron_status() -> dict:
    path = ROOT / "logs/poma-reconcile-cron.log"
    if not path.exists():
        return {"post_gateway_runs": 0, "last_result": "no_log", "successful_runs": 0}
    pattern = re.compile(r"^\[(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ)\] ===== command end: status=(\d+) =====")
    found = []
    for line in path.read_text(errors="replace").splitlines():
        match = pattern.match(line)
        if not match:
            continue
        when = datetime.fromisoformat(match.group(1).replace("Z", "+00:00"))
        if when >= RECOVERY_UTC:
            found.append((when.isoformat(), int(match.group(2))))
    return {
        "post_gateway_runs": len(found),
        "successful_runs": sum(code == 0 for _, code in found),
        "failed_runs": sum(code != 0 for _, code in found),
        "last_result": ("success" if found[-1][1] == 0 else "failed") if found else "not_run",
        "last_run_utc": found[-1][0] if found else None,
    }


def main() -> int:
    # Serialize with monitor/reconcile; never race broker queries or cause a replacement.
    import fcntl
    import os
    lock = ROOT / "state/poma-command.lock"
    descriptor = os.open(lock, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print('{"verification": "blocked", "reason": "POMA scheduled command busy"}')
        return 75
    try:
        status = cron_status()
        services = {"cron": service("cron"), "docker": service("docker"), "ibgateway": service("ibgateway")}
        image_env = ROOT / ".compose.env"
        image = ""
        if image_env.exists():
            match = re.search(r"^POMA_IMAGE=(.+)$", image_env.read_text(), re.M)
            image = match.group(1).strip() if match else ""
        image_ok = image.endswith(":d1c581e86c9bda6a9e2d9f70a0a003ec77c4f0e7")
        cmd = [
            "/usr/bin/docker", "compose", "--env-file", ".compose.env",
            "-f", "docker-compose.vm.yml", "run", "--rm", "--no-deps",
            "--entrypoint", "python", "poma", "-c", CODE,
        ]
        try:
            test = subprocess.run(
                cmd, cwd=ROOT, capture_output=True, text=True, timeout=110, check=False
            )
        except subprocess.TimeoutExpired:
            test = None
        observed = {}
        if test is not None:
            for line in test.stdout.splitlines():
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict) and "broker_order_query" in row:
                    observed = row
                    break
        observed.setdefault("broker_order_query", "timeout_or_unavailable")
        criteria = {
            "cron_service": services["cron"],
            "docker_service": services["docker"],
            "gateway_service": services["ibgateway"],
            "expected_image": image_ok,
            "post_recovery_cron_success": (
                status["successful_runs"] >= 1 and status["last_result"] == "success"
            ),
            "broker_open_orders_read": observed["broker_order_query"] == "success",
            "broker_poma_orders_clear": observed.get("broker_poma_open_count") == 0,
            "ledger_open_orders_clear": observed.get("ledger_open_count") == 0,
            "session_not_pending": observed.get("session_status") != "orders_pending",
        }
        print(json.dumps({
            "verified_at_utc": datetime.now(UTC).isoformat(),
            "services": services, "cron": status,
            "image_matches_deployment": image_ok, "broker": observed,
            "criteria": criteria, "ALL_GOOD": all(criteria.values()),
        }, sort_keys=True))
        return 0 if all(criteria.values()) else 2
    finally:
        os.close(descriptor)


if __name__ == "__main__":
    sys.exit(main())
