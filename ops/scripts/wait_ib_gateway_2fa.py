#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import subprocess
import time
from pathlib import Path

DIAGNOSE = "/usr/local/bin/poma-diagnose-ibgateway"
LOG_PATHS = (
    Path("/home/poma/ibc/logs"),
    Path("/var/log/poma/ibgateway"),
    Path("/tmp/poma-ibgateway"),
)
CHECKPOINT_PATH = Path("/var/lib/poma/ib-gateway-auth-log-checkpoint.json")
# Match runtime dialog/state events, never IBC's configuration dump or generic notifications.
TWO_FA_HINTS = re.compile(
    r"(?:detected|opened|displayed).*?(?:Second Factor Authentication|Security Code Card Authentication)|"
    r"(?:login state.*|setLoginState.*)TWO_FA_IN_PROGRESS|"
    r"(?:awaiting|waiting for).*?(?:2fa|second factor|mobile.*approval)",
    re.IGNORECASE,
)
STARTUP_STAGE = re.compile(r"^STARTUP_STAGE=(.*)$", re.MULTILINE)
STARTUP_ACTION = re.compile(r"^STARTUP_ACTION=(.*)$", re.MULTILINE)


def run(command: list[str], timeout: int = 60) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
    )


def checkpoint_logs() -> int:
    checkpoint = {}
    for directory in LOG_PATHS:
        if directory.exists():
            for path in directory.rglob("*"):
                if path.is_file() and not path.is_symlink():
                    st = path.stat()
                    checkpoint[str(path)] = [st.st_ino, st.st_size]
    CHECKPOINT_PATH.parent.mkdir(parents=True, exist_ok=True)
    CHECKPOINT_PATH.write_text(json.dumps(checkpoint), encoding="utf-8")
    print(f"checkpointed_gateway_auth_log_files={len(checkpoint)}")
    return 0


def tail_log_text(log_lines: int) -> str:
    checkpoint = json.loads(CHECKPOINT_PATH.read_text())
    chunks: list[str] = []
    for directory in LOG_PATHS:
        if not directory.exists():
            continue
        for path in sorted(directory.rglob("*")):
            if not path.is_file() or path.is_symlink():
                continue
            try:
                st = path.stat()
                inode, offset = checkpoint.get(str(path), [st.st_ino, 0])
                if inode != st.st_ino or st.st_size < offset:
                    offset = 0
                with path.open("rb") as handle:
                    # Bound memory even when Gateway logs are large.
                    handle.seek(max(offset, st.st_size - 256_000))
                    lines = handle.read().decode("utf-8", errors="replace").splitlines()
            except OSError:
                continue
            chunks.extend(lines[-log_lines:])
    return "\n".join(chunks)


def extract(pattern: re.Pattern[str], text: str, default: str = "unknown") -> str:
    match = pattern.search(text)
    return match.group(1).strip() if match else default


def print_progress(log_lines: int) -> None:
    result = run([DIAGNOSE, "progress", "--log-lines", str(log_lines)], timeout=90)
    print(result.stdout.rstrip())


def wait_for_2fa(
    *,
    timeout_seconds: int,
    poll_seconds: int,
    log_lines: int,
    fail_no_progress_after: int,
) -> int:
    deadline = time.monotonic() + timeout_seconds
    attempt = 0
    print("configure_requires_authenticated_api=true")
    print(f"Waiting up to {timeout_seconds}s for fresh IBKR mobile 2FA evidence on the VM.")
    while time.monotonic() < deadline:
        attempt += 1
        elapsed = max(0, int(timeout_seconds - (deadline - time.monotonic())))
        result = run(
            [
                DIAGNOSE,
                "startup-check",
                "--log-lines",
                str(log_lines),
                "--elapsed-seconds",
                str(elapsed),
                "--fail-no-progress-after",
                str(fail_no_progress_after),
            ],
            timeout=90,
        )
        print(f"===== Fresh 2FA startup classification {attempt} ({elapsed}s elapsed) =====")
        print(result.stdout.rstrip())
        stage = extract(STARTUP_STAGE, result.stdout)
        action = extract(STARTUP_ACTION, result.stdout)
        log_text = tail_log_text(log_lines)
        if TWO_FA_HINTS.search(log_text):
            print("Fresh IBKR mobile 2FA/login-auth evidence detected in current logs.")
            return 0
        if stage == "api-socket-open":
            print("Gateway API socket opened before fresh IBKR mobile 2FA evidence.")
            print("Broker may have resumed an authenticated session; API/account/trading checks are still required.")
            return 0
        if action == "fail":
            print(f"Gateway startup reached failing stage before fresh 2FA evidence: {stage}")
            print_progress(log_lines)
            return 2
        if elapsed > 0 and elapsed % 60 < poll_seconds:
            print_progress(log_lines)
        time.sleep(poll_seconds)
    print("No fresh IBKR mobile 2FA evidence appeared after configure.")
    print_progress(log_lines)
    return 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeout-seconds", type=int, default=360)
    parser.add_argument("--poll-seconds", type=int, default=5)
    parser.add_argument("--log-lines", type=int, default=80)
    parser.add_argument("--fail-no-progress-after", type=int, default=200)
    parser.add_argument("--checkpoint-logs-only", "--truncate-logs-only", action="store_true")
    args = parser.parse_args()
    if args.checkpoint_logs_only:
        return checkpoint_logs()
    if min(args.timeout_seconds, args.poll_seconds, args.log_lines, args.fail_no_progress_after) <= 0:
        parser.error("timeouts, poll interval and log lines must be positive")
    return wait_for_2fa(
        timeout_seconds=args.timeout_seconds,
        poll_seconds=args.poll_seconds,
        log_lines=args.log_lines,
        fail_no_progress_after=args.fail_no_progress_after,
    )


if __name__ == "__main__":
    raise SystemExit(main())
