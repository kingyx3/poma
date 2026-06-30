#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import pwd
import re
import shutil
import stat
import subprocess
from pathlib import Path
from typing import NamedTuple

IBC_CONFIG = Path("/home/poma/ibc/config.ini")
GATEWAYSTART = Path("/opt/ibc/gatewaystart.sh")
RUNNER = Path("/usr/local/bin/poma-run-ib-gateway")
SERVICE = Path("/etc/systemd/system/ibgateway.service")
LOG_PATHS = (
    Path("/home/poma/ibc/logs"),
    Path("/var/log/poma/ibgateway"),
    Path("/tmp/poma-ibgateway"),
)
PORTS = ("7497", "4001", "4002", "5900")
SENSITIVE_KEYS = {"IbLoginId", "IbPassword", "TWSUSERID", "TWSPASSWORD"}
_STARTUP_GRACE_STAGES = frozenset({"service-active-no-process"})
_STARTUP_LOG_NOISY_STAGES = frozenset({"gateway-log-error"})
PROCESS_RE = re.compile(r"ibgateway|gatewaystart|poma-ibc-gateway-engine|ibc|Xvfb|fluxbox|x11vnc|java", re.I)
TWO_FA_HINTS = re.compile(r"second factor|2fa|two-factor|mobile app|approve", re.I)
LOGIN_STAGE_HINTS = re.compile(r"login|authenticat|credentials|username|password", re.I)
FATAL_LOG_HINTS = re.compile(r"error|failed|exception|unable to|cannot|denied|fatal|oom|killed", re.I)


class StartupClassification(NamedTuple):
    stage: str
    action: str
    reason: str


def classify_startup_state(
    *,
    service_exists: bool,
    service_active: bool,
    api_socket_open: bool,
    config_exists: bool,
    has_xvfb: bool,
    has_fluxbox: bool,
    has_x11vnc: bool,
    has_gatewaystart: bool,
    has_java: bool,
    has_ibgateway: bool,
    log_text: str,
) -> StartupClassification:
    if api_socket_open:
        return StartupClassification("api-socket-open", "ready", "IB Gateway API port 7497 is listening.")
    if not service_exists:
        return StartupClassification("no-systemd-service", "fail", "ibgateway.service is not installed.")
    if not service_active:
        return StartupClassification("service-not-active", "fail", "ibgateway.service is not active.")
    if not has_xvfb:
        return StartupClassification("service-active-no-xvfb", "fail", "ibgateway.service is active but Xvfb is not running.")
    if not has_fluxbox or not has_x11vnc:
        return StartupClassification("headless-gui-incomplete", "fail", "headless display started but GUI sidecars are missing.")
    if config_exists and not has_gatewaystart:
        return StartupClassification("ibc-not-running", "fail", "IBC config exists but gatewaystart.sh is not running; Gateway likely never reached login.")
    if not (has_java or has_ibgateway):
        return StartupClassification("java-gateway-not-running", "fail", "No Java/IB Gateway process is running, so no IBKR mobile notification can be sent.")
    if TWO_FA_HINTS.search(log_text):
        return StartupClassification("login-reached-2fa-pending", "continue", "Gateway reached 2FA/login authorization, but API port 7497 is still closed.")
    if LOGIN_STAGE_HINTS.search(log_text):
        return StartupClassification("login-reached-awaiting-auth", "continue", "Gateway reached login/authentication, but API port 7497 is still closed.")
    if FATAL_LOG_HINTS.search(log_text):
        return StartupClassification("gateway-log-error", "continue", "Recent Gateway/IBC logs contain an error before API readiness.")
    return StartupClassification("gateway-running-no-api-socket", "continue", "Gateway support processes are running, but API port 7497 has not opened.")


def run(command: list[str], timeout: int = 20) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=False, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)


def section(title: str) -> None:
    print(f"===== {title} =====")


def redact(text: str) -> str:
    redacted = text
    for key in SENSITIVE_KEYS:
        redacted = re.sub(rf"(?im)^({re.escape(key)}\s*[=:]\s*).*", rf"\1***", redacted)
        redacted = re.sub(rf"(?i)({re.escape(key)}\s*[=:]\s*)[^\s]+", rf"\1***", redacted)
    return redacted


def print_command(title: str, command: list[str], timeout: int = 20) -> None:
    section(title)
    try:
        result = run(command, timeout=timeout)
    except subprocess.TimeoutExpired:
        print(f"command timed out after {timeout}s: {' '.join(command)}")
        return
    print(redact(result.stdout.rstrip()))
    if result.returncode != 0:
        print(f"exit={result.returncode}")


def read_key_values(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def command_exists(command: str) -> bool:
    return shutil.which(command) is not None


def listener_open(port: str = "7497") -> bool:
    return run(["nc", "-z", "127.0.0.1", port], timeout=10).returncode == 0


def service_exists() -> bool:
    return SERVICE.exists() or run(["systemctl", "cat", "ibgateway"], timeout=10).returncode == 0


def service_active() -> bool:
    return run(["systemctl", "is-active", "--quiet", "ibgateway"], timeout=10).returncode == 0


def process_summary() -> str:
    result = run(["ps", "auxww"], timeout=10)
    lines = [line for line in result.stdout.splitlines() if PROCESS_RE.search(line) and "diagnose_ib_gateway_runtime" not in line]
    return "\n".join(lines) if lines else "no Gateway/IBC/Xvfb/fluxbox/x11vnc/java processes found"


def process_flags(process_text: str) -> dict[str, bool]:
    return {
        "xvfb": bool(re.search(r"\bXvfb\b", process_text)),
        "fluxbox": bool(re.search(r"\bfluxbox\b", process_text)),
        "x11vnc": bool(re.search(r"\bx11vnc\b", process_text)),
        "gatewaystart": bool(re.search(r"poma-ibc-gateway-engine|gatewaystart\.sh", process_text)),
        "java": bool(re.search(r"\bjava\b", process_text, re.I)),
        "ibgateway": bool(re.search(r"\bibgateway\b", process_text, re.I)),
    }


def listener_summary() -> str:
    result = run(["ss", "-ltnp"], timeout=10)
    lines = [line for line in result.stdout.splitlines() if any(f":{port}" in line for port in PORTS)]
    return "\n".join(lines) if lines else "no relevant listeners on 7497/4001/4002/5900"


def tail_log_files(log_lines: int) -> list[tuple[Path, list[str]]]:
    tails: list[tuple[Path, list[str]]] = []
    for directory in LOG_PATHS:
        if not directory.exists():
            continue
        for path in sorted(directory.rglob("*")):
            if not path.is_file():
                continue
            try:
                tails.append((path, path.read_text(encoding="utf-8", errors="replace").splitlines()[-log_lines:]))
            except OSError:
                continue
    return tails


def recent_log_text(log_lines: int) -> str:
    chunks: list[str] = []
    for path, lines in tail_log_files(log_lines):
        chunks.append(f"--- {path} ---")
        chunks.extend(lines)
    return redact("\n".join(chunks))


def classify_startup(log_lines: int) -> StartupClassification:
    processes = process_summary()
    flags = process_flags(processes)
    return classify_startup_state(
        service_exists=service_exists(),
        service_active=service_active(),
        api_socket_open=listener_open("7497"),
        config_exists=IBC_CONFIG.exists(),
        has_xvfb=flags["xvfb"],
        has_fluxbox=flags["fluxbox"],
        has_x11vnc=flags["x11vnc"],
        has_gatewaystart=flags["gatewaystart"],
        has_java=flags["java"],
        has_ibgateway=flags["ibgateway"],
        log_text=recent_log_text(log_lines),
    )


def print_startup(log_lines: int) -> StartupClassification:
    classification = classify_startup(log_lines)
    section("Startup classification")
    print(f"STARTUP_STAGE={classification.stage}")
    print(f"STARTUP_ACTION={classification.action}")
    print(f"STARTUP_REASON={classification.reason}")
    return classification


def startup_check(log_lines: int, elapsed_seconds: int, fail_no_progress_after: int) -> int:
    classification = print_startup(log_lines)
    if classification.action == "ready":
        return 0
    if elapsed_seconds >= fail_no_progress_after:
        print("Startup progress deadline exceeded before API readiness; failing fast so full diagnostics are collected.")
        return 2
    if classification.action == "fail":
        return 2
    return 1


def validate_config(mode: str) -> int:
    section("IBC config validation")
    errors: list[str] = []
    if not IBC_CONFIG.exists():
        errors.append(f"missing {IBC_CONFIG}")
    else:
        st = IBC_CONFIG.stat()
        owner = pwd.getpwuid(st.st_uid).pw_name
        mode_bits = stat.S_IMODE(st.st_mode)
        print(f"{IBC_CONFIG}: owner={owner}, mode={mode_bits:o}")
        values = read_key_values(IBC_CONFIG)
        for key in sorted(values):
            print(f"{key}={'***' if key in SENSITIVE_KEYS else values[key]}")
        expected = {"TradingMode": mode, "OverrideTwsApiPort": "7497", "AcceptIncomingConnectionAction": "accept", "AllowBlindTrading": "yes", "ReloginAfterSecondFactorAuthenticationTimeout": "yes"}
        if owner != "poma":
            errors.append(f"{IBC_CONFIG} owner is {owner}, expected poma")
        if mode_bits != 0o600:
            errors.append(f"{IBC_CONFIG} mode is {mode_bits:o}, expected 600")
        for key in ("IbLoginId", "IbPassword"):
            if not values.get(key):
                errors.append(f"{key} is missing or empty")
        for key, value in expected.items():
            if values.get(key) != value:
                errors.append(f"{key}={values.get(key)!r}, expected {value!r}")
    for command in ("java", "Xvfb", "fluxbox", "x11vnc", "nc", "runuser", "ss"):
        print(f"{command}={'present' if command_exists(command) else 'missing'}")
        if not command_exists(command):
            errors.append(f"missing required command: {command}")
    print(f"gatewaystart={GATEWAYSTART}, executable={GATEWAYSTART.exists() and os.access(GATEWAYSTART, os.X_OK)}")
    print(f"runner={RUNNER}, executable={RUNNER.exists() and os.access(RUNNER, os.X_OK)}")
    if not GATEWAYSTART.exists() or not os.access(GATEWAYSTART, os.X_OK):
        errors.append(f"{GATEWAYSTART} is missing or not executable")
    if not RUNNER.exists() or not os.access(RUNNER, os.X_OK):
        errors.append(f"{RUNNER} is missing or not executable")
    if errors:
        section("Validation errors")
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print("IBC config, Gateway launcher, runtime commands, and systemd runner validation passed.")
    return 0


def progress(log_lines: int) -> int:
    print_command("ibgateway service active state", ["systemctl", "is-active", "ibgateway"], timeout=10)
    section("Gateway-related processes")
    print(redact(process_summary()))
    section("Relevant listening ports")
    print(redact(listener_summary()))
    print_startup(log_lines)
    section("Recent login/API log hints")
    text = recent_log_text(log_lines)
    if text:
        print(text)
    else:
        print("No login, 2FA, API, socket, or error hints found in recent Gateway/IBC logs.")
        print("If no mobile approval notification was received, Gateway/IBC likely has not reached the IBKR login/2FA stage yet.")
    return 0


def diagnose(log_lines: int) -> int:
    print_command("systemctl status ibgateway", ["systemctl", "status", "ibgateway", "--no-pager", f"--lines={log_lines}"], timeout=30)
    print_command("journalctl -u ibgateway", ["journalctl", "-u", "ibgateway", "-n", str(log_lines), "--no-pager"], timeout=30)
    progress(log_lines)
    section("Redacted IBC config")
    print(redact(IBC_CONFIG.read_text(encoding="utf-8", errors="replace")) if IBC_CONFIG.exists() else f"missing {IBC_CONFIG}")
    section("Patched gatewaystart.sh key settings")
    values = read_key_values(GATEWAYSTART)
    for key in ("IBC_INI", "TWS_SETTINGS_PATH", "LOG_PATH", "TWOFA_TIMEOUT_ACTION", "TWS_PATH", "TWS_MAJOR_VRSN"):
        print(f"{key}={values.get(key, '<missing>')}")
    section("Runtime env and permissions")
    for path in (*LOG_PATHS, Path("/home/poma/Jts"), Path("/home/poma/ibc"), Path("/run/poma-ibgateway")):
        print(f"{path}: exists={path.exists()}")
    for directory in LOG_PATHS:
        section(f"tail logs under {directory}")
        if not directory.exists():
            print("missing")
            continue
        for path, lines in tail_log_files(log_lines):
            if path.is_relative_to(directory):
                print(f"--- {path} ---")
                print(redact("\n".join(lines)))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate_parser = subparsers.add_parser("validate")
    validate_parser.add_argument("--mode", choices=("paper", "live"), required=True)
    progress_parser = subparsers.add_parser("progress")
    progress_parser.add_argument("--log-lines", type=int, default=40)
    startup_parser = subparsers.add_parser("startup-check")
    startup_parser.add_argument("--log-lines", type=int, default=40)
    startup_parser.add_argument("--elapsed-seconds", type=int, default=0)
    startup_parser.add_argument("--fail-no-progress-after", type=int, default=150)
    diagnose_parser = subparsers.add_parser("diagnose")
    diagnose_parser.add_argument("--log-lines", type=int, default=200)
    args = parser.parse_args()
    if args.command == "validate":
        return validate_config(args.mode)
    if args.command == "progress":
        return progress(args.log_lines)
    if args.command == "startup-check":
        return startup_check(args.log_lines, args.elapsed_seconds, args.fail_no_progress_after)
    return diagnose(args.log_lines)


if __name__ == "__main__":
    raise SystemExit(main())
