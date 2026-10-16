#!/usr/bin/env python3
"""Safe, low-overhead scheduled-command incident alerts (stdlib only).

Runs on the VM host after the command completes, not in the trading container.
Never reads broker credentials into the alert body or assumes broker order finality.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import socket
import sys
import time
import urllib.error
import urllib.request
from contextlib import suppress
from pathlib import Path

ANSI = re.compile(r"\x1b\[[0-9;]*m")
ACCOUNT = re.compile(r"\b(?:DU|U)\d{5,12}\b", re.IGNORECASE)
SENSITIVE = re.compile(
    r"(?i)\b(token|password|passwd|secret|api[_ -]?key|chat[_ -]?id|authorization|"
    r"ibkr_account|account_id|access_key|session[_ -]?id)\b(\s*[:=]\s*)([^\s,;]+)"
)
BOT_URL = re.compile(r"api\.telegram\.org/bot[^/\s]+", re.IGNORECASE)
BEARER = re.compile(r"(?i)\bBearer\s+\S+")
MAX_LOG_BYTES = 128_000
REPEAT_ALERT_SECONDS = 900

def scrub(value: object) -> str:
    line = ANSI.sub("", str(value)).replace("\r", " ").replace("\n", " ")
    line = BOT_URL.sub("api.telegram.org/bot[redacted]", line)
    line = BEARER.sub("Bearer [redacted]", line)
    line = SENSITIVE.sub(r"\1\2[redacted]", line)
    line = ACCOUNT.sub("[account-redacted]", line)
    return line[:240]

def classify(log_lines: list[str]) -> tuple[str, str, str]:
    """Stable classification. The excerpt is intentionally an allowlisted signature."""
    sample = "\n".join(log_lines).lower()
    if any(s in sample for s in ("connectionrefusederror", "connect call failed", "connection refused", "errno 111")):
        return (
            "IBKR_CONNECTION_REFUSED",
            "IBKR API refused a connection; Gateway may be starting, restarting or unauthenticated.",
            "ConnectionRefusedError / Connect call failed",
        )
    if "brokerunavailable" in sample or "unable to connect to ibkr api" in sample:
        return (
            "IBKR_UNAVAILABLE",
            "IBKR API handshake or broker availability failed; account/order state is not verified.",
            "BrokerUnavailable",
        )
    if "timeouterror" in sample or "timed out" in sample or "timeout" in sample:
        return (
            "TIMEOUT",
            "A scheduled operation timed out; verify IBKR Gateway and VM resource headroom.",
            "TimeoutError / timed out",
        )
    if "no space left on device" in sample:
        return ("DISK_FULL", "VM disk is full; check housekeeping and available space.", "No space left on device")
    if "cannot connect to the docker daemon" in sample or "docker daemon" in sample:
        return (
            "DOCKER_UNAVAILABLE",
            "Docker daemon is unavailable; the trading command could not be verified.",
            "Docker daemon unavailable",
        )
    if "permission denied" in sample:
        return ("PERMISSION_DENIED", "A command was denied access to a required host resource.", "Permission denied")
    if "validationerror" in sample or "missing required" in sample:
        return (
            "CONFIGURATION",
            "Application configuration validation failed.",
            "ValidationError / missing required configuration",
        )
    for line in reversed(log_lines):
        clean = scrub(line)
        match = re.search(r"\b([A-Za-z_][A-Za-z_0-9]*(?:Error|Exception))\s*:", clean)
        if match:
            return (
                "APP_EXCEPTION",
                "An application exception stopped the scheduled command; inspect the sanitized diagnostic log.",
                match.group(1),
            )
    return (
        "COMMAND_FAILED",
        "Command exited unsuccessfully; inspect the run-specific host log.",
        "Nonzero process exit",
    )

def recent_lines(log_path: Path, start_offset: int) -> list[str]:
    try:
        with log_path.open("rb") as handle:
            size = handle.seek(0, os.SEEK_END)
            if start_offset < 0 or start_offset > size:
                start_offset = max(0, size - MAX_LOG_BYTES)
            handle.seek(max(start_offset, size - MAX_LOG_BYTES))
            raw = handle.read(MAX_LOG_BYTES)
        return raw.decode("utf-8", errors="replace").splitlines()[-120:]
    except OSError:
        return []

def settings_from_env() -> dict[str, str]:
    settings: dict[str, str] = {}
    try:
        for raw in Path(".env").read_text().splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            settings[key.strip()] = value.strip().strip('"').strip("'")
    except OSError:
        pass
    return settings

def send_message(message: str) -> bool:
    settings = settings_from_env()
    token = os.getenv("TELEGRAM_BOT_TOKEN") or settings.get("TELEGRAM_BOT_TOKEN")
    chat = os.getenv("TELEGRAM_CHAT_ID") or settings.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        return False
    payload = json.dumps({"chat_id": chat, "text": message[:3900]}).encode()
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            response.read()
        return True
    except (OSError, urllib.error.URLError):
        return False

def local_order_exposure() -> str:
    """Local ledger is never treated as authoritative proof of broker order finality."""
    path = Path("state/orders/open_orders.jsonl")
    try:
        if not path.exists():
            return "Local ledger unavailable; broker orders unverified"
        if path.stat().st_size > 8_000_000:
            return "Local ledger too large to inspect; broker orders unverified"
        entries: dict[str, dict] = {}
        for line in path.read_text(errors="replace").splitlines():
            row = json.loads(line)
            key = row.get("ledger_key") or row.get("order_ref")
            if key:
                entries[str(key)] = row
        terminal = {"filled", "cancelled", "canceled", "rejected", "expired"}
        unresolved = sum(
            str(item.get("lifecycle_state", "")).lower() not in terminal
            for item in entries.values()
        )
        return f"Local unresolved ledger entries: {unresolved}; broker orders unverified"
    except (OSError, ValueError, TypeError):
        return "Local ledger unreadable; broker orders unverified"

def incident_path(command: str) -> Path:
    base = Path(os.getenv("POMA_CRON_ALERT_STATE_DIR", "state"))
    safe_name = re.sub(r"[^a-z0-9-]", "-", command.lower())[:40] or "unknown"
    return base / f"cron-alert-{safe_name}.json"

def read_state(path: Path) -> dict:
    try:
        value = json.loads(path.read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}

def write_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + f".{os.getpid()}.tmp")
    try:
        with temp.open("w") as stream:
            os.chmod(temp, 0o600)
            json.dump(state, stream)
        os.replace(temp, path)
    finally:
        with suppress(OSError):
            temp.unlink(missing_ok=True)

def report(log_path: Path, status: int, command: str, start_offset: int) -> None:
    now = int(time.time())
    path = incident_path(command)
    old = read_state(path)
    env = (os.environ.get("APP_ENV") or settings_from_env().get("APP_ENV") or "unknown").upper()
    host = scrub(socket.gethostname())
    if status == 0:
        if old.get("active"):
            started = int(old.get("first_failed_at", now))
            count = int(old.get("count", 0))
            message = "\n".join((
                f"📈 POMA · {env}", f"✅ Scheduled {command} recovered",
                f"Host: {host}",
                f"Previous failures: {count}",
                f"Incident duration: {max(0, now - started) // 60} min",
                "Command succeeded on this run. This does NOT independently certify every broker order's final state.",
            ))
            send_message(message)
        write_state(path, {"active": False, "last_success_at": now})
        return

    lines = recent_lines(log_path, start_offset)
    category, explanation, signature = classify(lines)
    fingerprint = hashlib.sha256(category.encode()).hexdigest()[:10]
    same = old.get("active") and old.get("fingerprint") == fingerprint
    count = int(old.get("count", 0)) + 1 if same else 1
    first = int(old.get("first_failed_at", now)) if same else now
    last_sent = int(old.get("last_alert_at", 0)) if same else 0
    notify = not same or count == 3 or now - last_sent >= REPEAT_ALERT_SECONDS
    new_state = {
        "active": True, "count": count, "fingerprint": fingerprint,
        "category": category, "first_failed_at": first,
        "last_failed_at": now, "last_alert_at": now if notify else last_sent,
        "last_success_at": old.get("last_success_at"),
    }
    write_state(path, new_state)
    if not notify:
        return
    last_success = old.get("last_success_at")
    success_at = (
        time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(last_success))
        if isinstance(last_success, (int, float)) else "unknown"
    )
    impact = (
        local_order_exposure()
        if command == "reconcile-orders" else "Review session status; execution outcome not certified"
    )
    diagnostics = (
        "DEV diagnostics: https://github.com/kingyx3/poma/actions/workflows/dev-reconcile-diagnostics.yml"
        if env == "DEV" and command == "reconcile-orders" else "Inspect VM logs and Gateway Ops workflow."
    )
    message = "\n".join((
        f"📈 POMA · {env}",
        f"🚨 Scheduled {command} failed",
        f"Host: {host} | Exit: {status}",
        f"Category: {category}",
        f"Cause: {explanation}",
        f"Error signature: {signature}",
        f"Consecutive failures: {count}",
        f"Last successful run: {success_at}",
        f"Order impact: {impact}",
        "Recovery: next scheduled invocation will retry; Gateway watchdog handles authenticated restart checks.",
        f"Host log: {scrub(log_path)}",
        diagnostics,
    ))
    send_message(message)

def main() -> int:
    if len(sys.argv) != 5:
        print("usage: alert_diagnostics.py LOG_PATH STATUS COMMAND START_OFFSET", file=sys.stderr)
        return 64
    log_path, status, command, start_offset = sys.argv[1:]
    if command not in ("monitor", "reconcile-orders"):
        command = "scheduled-command"
    report(Path(log_path), int(status), command, int(start_offset))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
