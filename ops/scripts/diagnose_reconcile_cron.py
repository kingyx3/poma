#!/usr/bin/env python3
"""Read-only sanitized diagnostics for failed POMA reconciliation cron commands."""
import json
import re
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
        for line in lines[max(0, start, end - 140):end + 1]:
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
for unit in ("docker", "cron", "ibgateway", "ibgateway-paper"):
    try:
        p = subprocess.run(["systemctl", "is-active", unit], capture_output=True, text=True, timeout=5, check=False)
        print(f"unit_{unit}={p.stdout.strip()}")
    except (OSError, subprocess.TimeoutExpired):
        print(f"unit_{unit}=unavailable")
try:
    p = subprocess.run(["docker", "ps", "--format", "{{.Names}} {{.Status}}"], capture_output=True, text=True, timeout=10, check=False)
    print("containers=", [safe(s) for s in p.stdout.splitlines()[:10]])
except (OSError, subprocess.TimeoutExpired):
    print("containers_unavailable")
