#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import pwd
import re
import stat
import subprocess
from pathlib import Path

IBC_CONFIG = Path("/home/poma/ibc/config.ini")
GATEWAYSTART = Path("/opt/ibc/gatewaystart.sh")
RUNNER = Path("/usr/local/bin/poma-run-ib-gateway")
SERVICE = Path("/etc/systemd/system/ibgateway.service")
LOG_PATHS = (
    Path("/home/poma/ibc/logs"),
    Path("/var/log/poma/ibgateway"),
    Path("/tmp/poma-ibgateway"),
)

SENSITIVE_KEYS = {
    "IbLoginId",
    "IbPassword",
    "TWSUSERID",
    "TWSPASSWORD",
    "FIXUSERID",
    "FIXPASSWORD",
}
LOGIN_HINTS = re.compile(
    r"login|auth|second factor|2fa|two-factor|invalid|failed|error|api|socket",
    re.IGNORECASE,
)


def run(command: list[str], timeout: int = 20) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
    )


def section(title: str) -> None:
    print(f"===== {title} =====")


def redact(text: str) -> str:
    redacted = text
    for key in SENSITIVE_KEYS:
        redacted = re.sub(
            rf"(?m)^({re.escape(key)}\s*=).*",
            rf"\1***",
            redacted,
        )
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


def require(condition: bool, message: str, errors: list[str]) -> None:
    if not condition:
        errors.append(message)


def validate_config(mode: str) -> int:
    section("IBC config validation")
    errors: list[str] = []
    require(IBC_CONFIG.exists(), f"missing {IBC_CONFIG}", errors)
    if IBC_CONFIG.exists():
        st = IBC_CONFIG.stat()
        owner = pwd.getpwuid(st.st_uid).pw_name
        mode_bits = stat.S_IMODE(st.st_mode)
        print(f"{IBC_CONFIG}: owner={owner}, mode={mode_bits:o}")
        require(owner == "poma", f"{IBC_CONFIG} owner is {owner}, expected poma", errors)
        require(mode_bits == 0o600, f"{IBC_CONFIG} mode is {mode_bits:o}, expected 600", errors)
        values = read_key_values(IBC_CONFIG)
        for key in sorted(values):
            display = "***" if key in SENSITIVE_KEYS else values[key]
            print(f"{key}={display}")
        for key in ("IbLoginId", "IbPassword"):
            require(bool(values.get(key)), f"{key} is missing or empty", errors)
        expected = {
            "TradingMode": mode,
            "OverrideTwsApiPort": "7497",
            "AcceptIncomingConnectionAction": "accept",
            "AllowBlindTrading": "yes",
            "ReloginAfterSecondFactorAuthenticationTimeout": "yes",
        }
        for key, value in expected.items():
            require(values.get(key) == value, f"{key}={values.get(key)!r}, expected {value!r}", errors)

    section("Gateway launcher validation")
    require(GATEWAYSTART.exists(), f"missing {GATEWAYSTART}", errors)
    if GATEWAYSTART.exists():
        values = read_key_values(GATEWAYSTART)
        for key in (
            "IBC_INI",
            "TWS_SETTINGS_PATH",
            "LOG_PATH",
            "TWOFA_TIMEOUT_ACTION",
            "TWS_PATH",
            "TWS_MAJOR_VRSN",
            "HIDE",
        ):
            print(f"{key}={values.get(key, '<missing>')}")
        require(values.get("IBC_INI") == str(IBC_CONFIG), "gatewaystart.sh does not point IBC_INI at config.ini", errors)
        require(values.get("TWS_SETTINGS_PATH") == "/home/poma/Jts", "gatewaystart.sh has unexpected TWS_SETTINGS_PATH", errors)
        require(values.get("LOG_PATH") == "/home/poma/ibc/logs", "gatewaystart.sh has unexpected LOG_PATH", errors)
        require(values.get("TWOFA_TIMEOUT_ACTION") == "exit", "gatewaystart.sh has unexpected TWOFA_TIMEOUT_ACTION", errors)
        require(bool(values.get("TWS_PATH")), "gatewaystart.sh TWS_PATH is empty", errors)
        require(bool(values.get("TWS_MAJOR_VRSN")), "gatewaystart.sh TWS_MAJOR_VRSN is empty", errors)

    section("Systemd runner validation")
    service_text = SERVICE.read_text(encoding="utf-8", errors="replace") if SERVICE.exists() else ""
    runner_text = RUNNER.read_text(encoding="utf-8", errors="replace") if RUNNER.exists() else ""
    print(f"service={SERVICE}, exists={SERVICE.exists()}")
    print(f"runner={RUNNER}, exists={RUNNER.exists()}, executable={os.access(RUNNER, os.X_OK)}")
    require("ExecStart=/usr/local/bin/poma-run-ib-gateway" in service_text, "systemd unit does not use poma-run-ib-gateway", errors)
    for snippet in ("Xvfb", "fluxbox", "x11vnc", "gatewaystart.sh", "-inline"):
        require(snippet in runner_text, f"runner missing {snippet}", errors)

    if errors:
        section("Validation errors")
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print("IBC config, gateway launcher, and systemd runner validation passed.")
    return 0


def listener_summary() -> str:
    result = run(["ss", "-ltnp"], timeout=10)
    lines = []
    for line in result.stdout.splitlines():
        if any(f":{port}" in line for port in ("7497", "4001", "4002", "5900")):
            lines.append(line)
    return "\n".join(lines) if lines else "no relevant listeners on 7497/4001/4002/5900"


def process_summary() -> str:
    result = run(["ps", "auxww"], timeout=10)
    lines = [
        line
        for line in result.stdout.splitlines()
        if re.search(r"ibgateway|gatewaystart|ibc|Xvfb|fluxbox|x11vnc|java", line, re.IGNORECASE)
        and "diagnose_ib_gateway_runtime" not in line
    ]
    return "\n".join(lines) if lines else "no Gateway/IBC/Xvfb/fluxbox/x11vnc/java processes found"


def log_hints(log_lines: int) -> list[str]:
    hints: list[str] = []
    for directory in LOG_PATHS:
        if not directory.exists():
            continue
        for path in sorted(directory.glob("*")):
            if not path.is_file():
                continue
            try:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-log_lines:]
            except OSError:
                continue
            matched = [line for line in lines if LOGIN_HINTS.search(line)]
            if matched:
                hints.append(f"{path}:")
                hints.extend(redact(line) for line in matched[-40:])
    return hints


def progress() -> int:
    print_command("ibgateway service active state", ["systemctl", "is-active", "ibgateway"], timeout=10)
    section("Gateway-related processes")
    print(redact(process_summary()))
    section("Relevant listening ports")
    print(redact(listener_summary()))
    hints = log_hints(40)
    section("Recent login/API log hints")
    if hints:
        print("\n".join(hints))
    else:
        print("No login, 2FA, API, socket, or error hints found in recent Gateway/IBC logs.")
        print("If no mobile approval notification was received, Gateway/IBC likely has not reached the IBKR login/2FA stage yet.")
    return 0


def diagnose(log_lines: int) -> int:
    print_command("systemctl status ibgateway", ["systemctl", "status", "ibgateway", "--no-pager", f"--lines={log_lines}"], timeout=30)
    print_command("journalctl -u ibgateway", ["journalctl", "-u", "ibgateway", "-n", str(log_lines), "--no-pager"], timeout=30)
    progress()
    section("Redacted IBC config")
    if IBC_CONFIG.exists():
        print(redact(IBC_CONFIG.read_text(encoding="utf-8", errors="replace")))
    else:
        print(f"missing {IBC_CONFIG}")
    section("Patched gatewaystart.sh key settings")
    if GATEWAYSTART.exists():
        values = read_key_values(GATEWAYSTART)
        for key in sorted(k for k in values if k in {"IBC_INI", "TWS_SETTINGS_PATH", "LOG_PATH", "TWOFA_TIMEOUT_ACTION", "TWS_PATH", "TWS_MAJOR_VRSN", "TRADING_MODE", "HIDE"} or k in SENSITIVE_KEYS):
            value = "***" if key in SENSITIVE_KEYS else values[key]
            print(f"{key}={value}")
    else:
        print(f"missing {GATEWAYSTART}")
    for directory in LOG_PATHS:
        section(f"tail logs under {directory}")
        if not directory.exists():
            print("missing")
            continue
        for path in sorted(directory.glob("*")):
            if path.is_file():
                print(f"--- {path} ---")
                try:
                    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-log_lines:]
                except OSError as exc:
                    print(f"cannot read: {exc}")
                    continue
                print(redact("\n".join(lines)))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate_parser = subparsers.add_parser("validate")
    validate_parser.add_argument("--mode", choices=("paper", "live"), required=True)
    progress_parser = subparsers.add_parser("progress")
    progress_parser.add_argument("--log-lines", type=int, default=40)
    diagnose_parser = subparsers.add_parser("diagnose")
    diagnose_parser.add_argument("--log-lines", type=int, default=200)
    args = parser.parse_args()
    if args.command == "validate":
        return validate_config(args.mode)
    if args.command == "progress":
        return progress()
    return diagnose(args.log_lines)


if __name__ == "__main__":
    raise SystemExit(main())
