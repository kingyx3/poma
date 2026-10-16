#!/usr/bin/env python3
"""Read-only sanitized diagnostics for failed POMA reconciliation cron commands."""
import json
import re
import socket
import subprocess
from collections import Counter
from pathlib import Path

root = Path("/opt/poma")
log = root / "logs/poma-reconcile-cron.log"
redact = re.compile(r"(?i)(token|password|secret|api[_ -]?key|chat[_ -]?id|authorization)(\s*[:=]\s*)(\S+)")
account = re.compile(r"\b(?:DU|U)\d{5,12}\b")

def safe(s):
    return account.sub("[account-redacted]", redact.sub(r"\1\2[redacted]", s))[:480]

print("=== Read-only DEV reconciliation diagnostics ===")
print("log_present=", log.exists())
if log.exists():
    lines = log.read_text(errors="replace").splitlines()
    outcomes = [(i, line) for i, line in enumerate(lines) if "===== command end: status=" in line]
    print("last_20_runs=", dict(Counter("ok" if "status=0" in s else "failed" for _, s in outcomes[-20:])))
    failures = [i for i, s in outcomes if "status=0" not in s]
    if failures:
        end = failures[-1]
        start = next((i for i in range(end, -1, -1) if "===== command start:" in lines[i]), end - 140)
        print("=== Last failed command output (redacted) ===")
        # Emit error signatures only; Rich traceback locals contain sensitive
        # configuration and must never be copied to GitHub Actions logs.
        for line in lines[max(0, start, end - 140):end + 1]:
            if re.search(
                r"(?i)BrokerUnavailable|ConnectionRefused|TimeoutError|OSError|"
                r"RuntimeError|ValueError|PermissionError|Traceback|"
                r"connect to IBKR|Connect call failed|command end: status=",
                line,
            ):
                print(safe(line))
    else:
        print("=== Recent output ===")
        for line in lines[-35:]:
            print(safe(line))
state = root / "state/rebalance_state.json"
try:
    s = json.loads(state.read_text())
    print("session=", s.get("last_rebalance_session"), "status=", s.get("last_rebalance_status"))
except (OSError, ValueError):
    print("session_state_unavailable")
ledger = root / "state/orders/open_orders.jsonl"
counts = Counter()
if ledger.exists():
    for line in ledger.read_text(errors="replace").splitlines():
        try:
            row = json.loads(line)
            counts[row.get("lifecycle_state", "unknown")] += 1
        except (ValueError, AttributeError):
            counts["corrupt"] += 1
print("open_order_lifecycle_counts=", dict(counts))
# Distinguish a running systemd supervisor from a listening/authenticated API.
try:
    with socket.create_connection(("127.0.0.1", 7497), timeout=1):
        print("ibkr_api_socket_7497=LISTENING (TCP only; login not verified)")
except OSError as exc:
    print(f"ibkr_api_socket_7497=UNAVAILABLE ({type(exc).__name__})")

# Only emit classified Gateway startup hints, never raw login logs or secrets.
gateway_log = root / "logs/ibgateway/gatewaystart-wrapper.log"
if not gateway_log.exists():
    gateway_log = Path("/var/log/poma/ibgateway/gatewaystart-wrapper.log")
if gateway_log.exists():
    hints = Counter()
    for line in gateway_log.read_text(errors="replace").splitlines()[-300:]:
        lowered = line.lower()
        if "2fa" in lowered or "second factor" in lowered or "mobile app" in lowered:
            hints["2fa"] += 1
        if "login" in lowered or "authenticat" in lowered:
            hints["login_or_auth"] += 1
        if "api" in lowered or "socket" in lowered or "7497" in lowered:
            hints["api_or_socket"] += 1
        if "failed" in lowered or "error" in lowered or "denied" in lowered:
            hints["error"] += 1
        if "restart" in lowered or "exiting" in lowered or "starting" in lowered:
            hints["start_or_restart"] += 1
    print("gateway_startup_hints_last_300_lines=", dict(hints))
else:
    print("gateway_startup_log_unavailable")
for unit in ("docker", "cron", "ibgateway", "ibgateway-paper"):
    try:
        p = subprocess.run(["systemctl", "is-active", unit], capture_output=True, text=True, timeout=5, check=False)
        print(f"unit_{unit}={p.stdout.strip()}")
    except (OSError, subprocess.TimeoutExpired):
        print(f"unit_{unit}=unavailable")
try:
    p = subprocess.run(
        ["docker", "ps", "--format", "{{.Names}} {{.Status}}"],
        capture_output=True, text=True, timeout=10, check=False,
    )
    print("containers=", [safe(s) for s in p.stdout.splitlines()[:10]])
except (OSError, subprocess.TimeoutExpired):
    print("containers_unavailable")
