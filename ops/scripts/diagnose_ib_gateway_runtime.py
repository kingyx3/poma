#!/usr/bin/env python3
# ruff: noqa
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
IB_GATEWAY_DIR = Path("/opt/ibgateway")
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
    r"login|auth|second factor|2fa|two-factor|mobile|invalid|failed|error|api|socket|timeout",
    re.IGNORECASE,
)
FATAL_LOG_HINTS = re.compile(
    r"exception|traceback|segmentation|unable to|cannot|could not|no such file|"
    r"permission denied|invalid|failed|fatal|exited|exit code|oom|killed",
    re.IGNORECASE,
)
TWO_FA_HINTS = re.compile(
    r"second factor|2fa|two-factor|mobile authentication|mobile app|approve",
    re.IGNORECASE,
)
LOGIN_STAGE_HINTS = re.compile(r"login|authenticat|credentials|username|password", re.IGNORECASE)


class StartupClassification(NamedTuple):
    stage: str
    action: str
    reason: str


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
            rf"(?im)^({re.escape(key)}\s*[=:]\s*).*",
            rf"\1***",
            redacted,
        )
        redacted = re.sub(
            rf"(?i)({re.escape(key)}\s*[=:]\s*)[^\s]+",
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


def command_exists(command: str) -> bool:
    return shutil.which(command) is not None


def service_exists() -> bool:
    return SERVICE.exists() or run(["systemctl", "cat", "ibgateway"], timeout=10).returncode == 0


def service_active() -> bool:
    return run(["systemctl", "is-active", "--quiet", "ibgateway"], timeout=10).returncode == 0


def gateway_artifacts() -> tuple[list[Path], list[Path]]:
    executables = [
        path
        for path in IB_GATEWAY_DIR.glob("**/ibgateway")
        if path.is_file() and os.access(path, os.X_OK)
    ]
    jars_dirs = [
        path
        for path in IB_GATEWAY_DIR.glob("**/jars")
        if path.is_dir() and any(path.glob("*.jar"))
    ]
    return sorted(executables), sorted(jars_dirs)


def gateway_program_dir(values: dict[str, str]) -> Path | None:
    tws_path = values.get("TWS_PATH")
    version = values.get("TWS_MAJOR_VRSN")
    if not tws_path or not version:
        return None
    return Path(tws_path) / "ibgateway" / version


def user_writable(path: Path, user: str = "poma") -> bool:
    if not path.exists():
        return False
    return run(["runuser", "-u", user, "--", "test", "-w", str(path)], timeout=10).returncode == 0


def display_lock_stale(display: str = ":99") -> bool:
    display_num = display.lstrip(":")
    lock = Path(f"/tmp/.X{display_num}-lock")
    if not lock.exists():
        return False
    return "Xvfb" not in process_summary()


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

    section("Gateway install validation")
    executables, jars_dirs = gateway_artifacts()
    print("gateway executables:")
    print("\n".join(str(path) for path in executables) if executables else "<none>")
    print("gateway jars directories:")
    print("\n".join(str(path) for path in jars_dirs) if jars_dirs else "<none>")
    require(bool(executables or jars_dirs), f"no IB Gateway artifacts found under {IB_GATEWAY_DIR}", errors)
    for command in ("java", "Xvfb", "fluxbox", "x11vnc", "nc", "runuser"):
        print(f"{command}={'present' if command_exists(command) else 'missing'}")
        require(command_exists(command), f"missing required command: {command}", errors)
    java_result = run(["java", "-version"], timeout=20) if command_exists("java") else None
    if java_result is not None:
        print(redact(java_result.stdout.rstrip()))
        require(java_result.returncode == 0, "java -version failed", errors)

    section("Gateway launcher validation")
    require(GATEWAYSTART.exists(), f"missing {GATEWAYSTART}", errors)
    if GATEWAYSTART.exists():
        launcher_mode = stat.S_IMODE(GATEWAYSTART.stat().st_mode)
        print(f"{GATEWAYSTART}: mode={launcher_mode:o}, executable={os.access(GATEWAYSTART, os.X_OK)}")
        require(os.access(GATEWAYSTART, os.X_OK), f"{GATEWAYSTART} is not executable", errors)
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
        program_dir = gateway_program_dir(values)
        print(f"resolved gateway program dir={program_dir or '<unresolved>'}")
        require(program_dir is not None, "cannot resolve gateway program dir from TWS_PATH/TWS_MAJOR_VRSN", errors)
        if program_dir is not None:
            require(program_dir.exists(), f"resolved gateway program dir missing: {program_dir}", errors)
            require((program_dir / "jars").exists(), f"resolved gateway jars dir missing: {program_dir / 'jars'}", errors)
            require((program_dir / "ibgateway.vmoptions").exists(), f"missing {program_dir / 'ibgateway.vmoptions'}", errors)

    section("Runtime directory validation")
    for path in (Path("/home/poma/Jts"), Path("/home/poma/ibc/logs")):
        print(f"{path}: exists={path.exists()}, writable_by_poma={user_writable(path)}")
        require(path.exists(), f"missing runtime directory: {path}", errors)
        require(user_writable(path), f"{path} is not writable by poma", errors)
    require(not display_lock_stale(":99"), "stale /tmp/.X99-lock exists but no Xvfb process is running", errors)

    section("Systemd runner validation")
    service_text = SERVICE.read_text(encoding="utf-8", errors="replace") if SERVICE.exists() else ""
    runner_text = RUNNER.read_text(encoding="utf-8", errors="replace") if RUNNER.exists() else ""
    print(f"service={SERVICE}, exists={SERVICE.exists()}")
    print(f"runner={RUNNER}, exists={RUNNER.exists()}, executable={os.access(RUNNER, os.X_OK)}")
    require("ExecStart=/usr/local/bin/poma-run-ib-gateway" in service_text, "systemd unit does not use poma-run-ib-gateway", errors)
    for snippet in ("Xvfb", "fluxbox", "x11vnc", "gatewaystart.sh", "-inline"):
        require(snippet in runner_text, f"runner missing {snippet}", errors)
    require("Config exists but /opt/ibc/gatewaystart.sh is missing" in runner_text, "runner can silently fall back to raw Gateway when IBC config exists", errors)

    if errors:
        section("Validation errors")
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print("IBC config, Gateway install/layout, launcher, runtime, and systemd runner validation passed.")
    return 0


def listener_summary() -> str:
    result = run(["ss", "-ltnp"], timeout=10)
    lines = []
    for line in result.stdout.splitlines():
        if any(f":{port}" in line for port in ("7497", "4001", "4002", "5900")):
            lines.append(line)
    return "\n".join(lines) if lines else "no relevant listeners on 7497/4001/4002/5900"


def listener_open(port: str) -> bool:
    return run(["nc", "-z", "127.0.0.1", port], timeout=10).returncode == 0


def process_summary() -> str:
    result = run(["ps", "auxww"], timeout=10)
    lines = [
        line
        for line in result.stdout.splitlines()
        if re.search(r"ibgateway|gatewaystart|ibc|Xvfb|fluxbox|x11vnc|java", line, re.IGNORECASE)
        and "diagnose_ib_gateway_runtime" not in line
    ]
    return "\n".join(lines) if lines else "no Gateway/IBC/Xvfb/fluxbox/x11vnc/java processes found"


def process_flags(process_text: str) -> dict[str, bool]:
    return {
        "xvfb": bool(re.search(r"\bXvfb\b", process_text)),
        "fluxbox": bool(re.search(r"\bfluxbox\b", process_text)),
        "x11vnc": bool(re.search(r"\bx11vnc\b", process_text)),
        "gatewaystart": bool(re.search(r"gatewaystart\.sh", process_text)),
        "java": bool(re.search(r"\bjava\b", process_text, re.IGNORECASE)),
        "ibgateway": bool(re.search(r"\bibgateway\b", process_text, re.IGNORECASE)),
    }


def tail_log_files(log_lines: int) -> list[tuple[Path, list[str]]]:
    tails: list[tuple[Path, list[str]]] = []
    for directory in LOG_PATHS:
        if not directory.exists():
            continue
        for path in sorted(directory.rglob("*")):
            if not path.is_file():
                continue
            try:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-log_lines:]
            except OSError:
                continue
            tails.append((path, lines))
    return tails


def recent_log_text(log_lines: int) -> str:
    chunks = []
    for path, lines in tail_log_files(log_lines):
        chunks.append(f"--- {path} ---")
        chunks.extend(lines)
    return redact("\n".join(chunks))


def log_hints(log_lines: int) -> list[str]:
    hints: list[str] = []
    for path, lines in tail_log_files(log_lines):
        matched = [line for line in lines if LOGIN_HINTS.search(line)]
        if matched:
            hints.append(f"{path}:")
            hints.extend(redact(line) for line in matched[-40:])
    return hints


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
    if not service_exists:
        return StartupClassification(
            "no-systemd-service",
            "fail",
            "ibgateway.service is not installed; runtime repair did not complete.",
        )
    if api_socket_open:
        return StartupClassification(
            "api-socket-open",
            "ready",
            "IB Gateway API port 7497 is listening; proceed to the real API handshake.",
        )
    if not service_active:
        return StartupClassification(
            "service-not-active",
            "fail",
            "ibgateway.service is not active, so Gateway cannot reach login or 2FA.",
        )
    if not has_xvfb:
        return StartupClassification(
            "service-active-no-xvfb",
            "fail",
            "ibgateway.service is active but the headless Xvfb display is not running.",
        )
    if not has_fluxbox or not has_x11vnc:
        missing = ", ".join(
            name
            for name, present in (("fluxbox", has_fluxbox), ("x11vnc", has_x11vnc))
            if not present
        )
        return StartupClassification(
            "headless-gui-incomplete",
            "fail",
            f"headless display started but GUI sidecars are missing: {missing}.",
        )
    if config_exists and not has_gatewaystart:
        return StartupClassification(
            "ibc-not-running",
            "fail",
            "IBC config exists but gatewaystart.sh is not running; Gateway likely never reached login.",
        )
    if not (has_java or has_ibgateway):
        return StartupClassification(
            "java-gateway-not-running",
            "fail",
            "No Java/IB Gateway process is running, so no IBKR mobile notification can be sent.",
        )
    if FATAL_LOG_HINTS.search(log_text):
        return StartupClassification(
            "gateway-log-error",
            "fail",
            "Recent IBC/Gateway logs contain a fatal startup/login error before API readiness.",
        )
    if TWO_FA_HINTS.search(log_text):
        return StartupClassification(
            "login-reached-2fa-pending",
            "continue",
            "Gateway reached the broker authentication/2FA stage; wait for mobile approval.",
        )
    if LOGIN_STAGE_HINTS.search(log_text):
        return StartupClassification(
            "login-reached-awaiting-auth",
            "continue",
            "Gateway reached login/authentication but has not opened the API socket yet.",
        )
    if config_exists and has_gatewaystart and (has_java or has_ibgateway):
        return StartupClassification(
            "gateway-running-no-login-progress",
            "continue",
            "IBC/Gateway is running, but logs do not yet show login, 2FA, or API progress.",
        )
    return StartupClassification(
        "gateway-starting",
        "continue",
        "Gateway startup is still in progress.",
    )


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


def print_startup_classification(classification: StartupClassification) -> None:
    section("Startup classification")
    print(f"STARTUP_STAGE={classification.stage}")
    print(f"STARTUP_ACTION={classification.action}")
    print(f"STARTUP_REASON={classification.reason}")


def startup_check(log_lines: int, elapsed_seconds: int, fail_no_progress_after: int) -> int:
    classification = classify_startup(log_lines)
    if (
        classification.stage == "gateway-running-no-login-progress"
        and elapsed_seconds >= fail_no_progress_after
    ):
        classification = StartupClassification(
            "gateway-running-no-login-progress-timeout",
            "fail",
            "IBC/Gateway stayed alive but did not show login, 2FA, or API progress before the "
            f"{fail_no_progress_after}s startup-progress deadline.",
        )
    print_startup_classification(classification)
    if classification.action == "ready":
        return 0
    if classification.action == "fail":
        return 2
    return 1


def progress(log_lines: int = 40) -> int:
    print_command("ibgateway service active state", ["systemctl", "is-active", "ibgateway"], timeout=10)
    section("Gateway-related processes")
    print(redact(process_summary()))
    section("Relevant listening ports")
    print(redact(listener_summary()))
    print_startup_classification(classify_startup(log_lines))
    hints = log_hints(log_lines)
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
    progress(log_lines)
    section("Redacted IBC config")
    if IBC_CONFIG.exists():
        print(redact(IBC_CONFIG.read_text(encoding="utf-8", errors="replace")))
    else:
        print(f"missing {IBC_CONFIG}")
    section("Patched gatewaystart.sh key settings")
    if GATEWAYSTART.exists():
        values = read_key_values(GATEWAYSTART)
        interesting = {
            "IBC_INI",
            "TWS_SETTINGS_PATH",
            "LOG_PATH",
            "TWOFA_TIMEOUT_ACTION",
            "TWS_PATH",
            "TWS_MAJOR_VRSN",
            "TRADING_MODE",
            "HIDE",
            "JAVA_PATH",
        }
        for key in sorted(k for k in values if k in interesting or k in SENSITIVE_KEYS):
            value = "***" if key in SENSITIVE_KEYS else values[key]
            print(f"{key}={value}")
        program_dir = gateway_program_dir(values)
        print(f"resolved gateway program dir={program_dir or '<unresolved>'}")
        if program_dir is not None:
            print(f"program dir exists={program_dir.exists()}")
            print(f"jars dir exists={(program_dir / 'jars').exists()}")
            print(f"vmoptions exists={(program_dir / 'ibgateway.vmoptions').exists()}")
    else:
        print(f"missing {GATEWAYSTART}")
    section("Runtime env and permissions")
    for path in (
        Path("/home/poma/Jts"),
        Path("/home/poma/ibc"),
        Path("/home/poma/ibc/logs"),
        Path("/run/poma-ibgateway"),
        Path("/var/log/poma/ibgateway"),
        Path("/tmp/poma-ibgateway"),
    ):
        if path.exists():
            st = path.stat()
            owner = pwd.getpwuid(st.st_uid).pw_name
            mode_bits = stat.S_IMODE(st.st_mode)
            print(f"{path}: owner={owner}, mode={mode_bits:o}, writable_by_poma={user_writable(path)}")
        else:
            print(f"{path}: missing")
    print(f"DISPLAY={os.environ.get('DISPLAY', '<unset>')}")
    print(f"stale_x99_lock={display_lock_stale(':99')}")
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
