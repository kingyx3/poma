#!/usr/bin/env python3
# ruff: noqa: E501, F401, F841
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HELPER_SCRIPTS = [
    "ops/scripts/repair_ib_gateway_runtime.py",
    "ops/scripts/install_ibc_config_helper.py",
    "ops/scripts/ensure_ibgateway_service.sh",
    "ops/scripts/diagnose_ib_gateway_runtime.py",
    "ops/scripts/wait_ib_gateway_2fa.py",
]


def env(name: str, default: str | None = None) -> str:
    value = os.environ.get(name, default)
    if value is None or value == "":
        raise SystemExit(f"missing required env: {name}")
    return value


def run(command: list[str], *, timeout: int = 180, input_text: str | None = None) -> int:
    print("+", " ".join(command), flush=True)
    try:
        completed = subprocess.run(
            command,
            check=False,
            text=True,
            input=input_text,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        print(f"command timed out after {timeout}s: {' '.join(command)}", file=sys.stderr)
        return 124
    return completed.returncode


def timed(label: str, func) -> int:
    print(f"::group::{label}")
    start = time.monotonic()
    status = func()
    elapsed = int(time.monotonic() - start)
    print("::endgroup::")
    print(f"TIMING {label}: {elapsed}s (status={status})")
    return status


def helper_revision() -> str:
    digest = hashlib.sha256()
    for script in HELPER_SCRIPTS:
        digest.update(Path(script).read_bytes())
    return digest.hexdigest()


def main() -> int:
    deploy_environment = env("DEPLOY_ENVIRONMENT")
    action = env("INPUT_ACTION")
    log_lines = env("LOG_LINES", "200")
    project_id = env("GCP_PROJECT_ID")
    zone = env("GCP_ZONE")
    vm_name = env("GCP_VM_NAME")
    twofa_timeout = env("IB_GATEWAY_2FA_APPROVAL_TIMEOUT_SECONDS", "360")
    poll_seconds = env("IB_GATEWAY_SOCKET_POLL_SECONDS", "5")
    no_progress_after = env("IB_GATEWAY_LOGIN_PROGRESS_GRACE_SECONDS", "200")
    ssh_common = ["--zone", zone, "--tunnel-through-iap", "--ssh-key-expire-after=15m", "--quiet"]
    sentinel = "/var/lib/poma/ib-gateway-runtime-revision"
    revision = helper_revision()

    def gcloud(*args: str, timeout: int = 180, input_text: str | None = None) -> int:
        return run(["gcloud", *args], timeout=timeout, input_text=input_text)

    def remote(command: str, timeout: int = 180) -> int:
        return gcloud("compute", "ssh", vm_name, *ssh_common, "--command", command, timeout=timeout)

    def repair_runtime() -> int:
        check = (
            f"test -f '{sentinel}' && [ \"$(cat '{sentinel}')\" = '{revision}' ] && "
            "test -x /usr/local/bin/poma-configure-ibc && "
            "test -x /usr/local/bin/poma-run-ib-gateway && "
            "test -x /usr/local/bin/poma-diagnose-ibgateway && "
            "test -x /usr/local/bin/poma-wait-ibgateway-2fa && "
            "systemctl cat ibgateway >/dev/null"
        )
        if timed("IAP SSH/runtime sentinel check", lambda: remote(check, timeout=75)) == 0:
            print(f"Gateway runtime helpers already current ({revision}); skipping repair/install.")
            return 0
        print("Gateway runtime sentinel missing or stale; fail-open by running repair/install.")
        upload = gcloud("compute", "scp", *HELPER_SCRIPTS, f"{vm_name}:/tmp/", *ssh_common, timeout=180)
        print(f"TIMING Upload gateway helper scripts: status={upload}")
        if upload != 0:
            return upload
        install = (
            "sudo python3 /tmp/repair_ib_gateway_runtime.py && "
            "sudo python3 /tmp/install_ibc_config_helper.py && "
            "sudo install -m 755 /tmp/diagnose_ib_gateway_runtime.py /usr/local/bin/poma-diagnose-ibgateway && "
            "sudo install -m 755 /tmp/wait_ib_gateway_2fa.py /usr/local/bin/poma-wait-ibgateway-2fa && "
            "sudo sh /tmp/ensure_ibgateway_service.sh && "
            "sudo install -d -m 755 /var/lib/poma && "
            f"printf '%s\n' '{revision}' | sudo tee '{sentinel}' >/dev/null"
        )
        return timed("Runtime repair/install", lambda: remote(install, timeout=900))

    def diagnose() -> None:
        command = (
            f"sudo poma-diagnose-ibgateway startup-check --log-lines 80 --elapsed-seconds {twofa_timeout} "
            f"--fail-no-progress-after {no_progress_after} || true; "
            "sudo poma-diagnose-ibgateway progress --log-lines 80 || true; "
            f"sudo poma-diagnose-ibgateway diagnose --log-lines {log_lines}"
        )
        timed("Collect gateway diagnostics", lambda: remote(command, timeout=240))

    def restart_gateway_for_trading_login(reason: str) -> None:
        print(reason)
        print(
            "Restarting IB Gateway to force a fresh primary trading/market-data login. "
            "Approve IBKR Mobile if prompted; this may disconnect any other active trading session."
        )
        timed(
            "Restart ibgateway for trading-enabled login",
            lambda: remote("sudo systemctl restart ibgateway", timeout=240),
        )

    def gateway_startup_progress(elapsed_seconds: int) -> int:
        command = (
            "sudo poma-diagnose-ibgateway startup-check --log-lines 80 "
            f"--elapsed-seconds {elapsed_seconds} "
            f"--fail-no-progress-after {no_progress_after}"
        )
        return timed("Gateway startup progress check", lambda: remote(command, timeout=120))

    def api_ready(mode: str, required: bool) -> int:
        timeout_seconds = int(twofa_timeout)
        poll_interval_seconds = int(poll_seconds)
        max_trading_login_restarts = 2
        started = time.monotonic()
        # Overall safety cap so bounded per-login restarts can never overrun the job timeout.
        hard_deadline = started + 2 * timeout_seconds
        # login_started tracks the current login attempt; a forced restart resets it so the fresh
        # login gets its own readiness/no-progress budget instead of inheriting a spent one.
        login_started = started
        deadline = min(hard_deadline, started + timeout_seconds)
        stable = 0
        attempt = 1
        restarts = 0
        print(f"Waiting up to {timeout_seconds}s per login attempt for broker auth and Gateway API trading readiness.")
        while time.monotonic() < deadline:
            elapsed_seconds = int(time.monotonic() - login_started)
            poll = timed(
                f"Socket/service poll attempt {attempt}",
                lambda: remote(
                    "if nc -z 127.0.0.1 7497; then exit 0; fi; if ! systemctl is-active --quiet ibgateway; then exit 2; fi; exit 1",
                    timeout=45,
                ),
            )
            if poll == 0:
                stable += 1
                if stable >= 2:
                    # Run the readiness what-if against the deployed VM image (docker-compose.vm.yml,
                    # host-networked) so it reuses the pulled image instead of building from source on
                    # the e2-micro and so 127.0.0.1:7497 reaches the host Gateway.
                    check = (
                        "if ! test -f /opt/poma/docker-compose.vm.yml; then "
                        + ("echo 'POMA app not deployed at /opt/poma (missing docker-compose.vm.yml)' >&2; exit 1; " if required else "echo 'POMA app not deployed at /opt/poma; skipping.'; exit 0; ")
                        + "fi; sudo -u poma bash -lc 'cd /opt/poma && docker compose --env-file .compose.env -f docker-compose.vm.yml run --rm -e TRADING_MODE="
                        + mode
                        + " -e DATA_PROVIDER=fixture poma ibkr-check'"
                    )
                    check_status = remote(check, timeout=240)
                    if check_status == 0:
                        print("IBKR API handshake and trading permission preview succeeded; Gateway is ready to submit orders.")
                        return 0
                    if restarts >= max_trading_login_restarts:
                        print(
                            "ERROR: the IBKR API socket is open but the what-if trading preview still fails "
                            f"after {restarts} forced fresh-login restart(s). The Gateway keeps logging in "
                            "read-only or without Trading/Market-Data permissions, which a restart cannot fix. "
                            "Verify the IBKR account's API settings have 'Read-Only API' disabled and that "
                            f"Trading and Market Data subscriptions are active for the {mode} account.",
                            file=sys.stderr,
                        )
                        diagnose()
                        return 1
                    restarts += 1
                    restart_gateway_for_trading_login(
                        "IBKR API socket opened but trading readiness failed. This usually means Gateway "
                        "logged in read-only / without Trading/Market-Data permissions. Forcing a fresh "
                        f"trading-enabled login (restart {restarts}/{max_trading_login_restarts})."
                    )
                    # Reset the per-login clocks so the fresh login gets a full, bounded readiness window.
                    stable = 0
                    login_started = time.monotonic()
                    deadline = min(hard_deadline, login_started + timeout_seconds)
            elif poll == 1:
                stable = 0
                startup_status = gateway_startup_progress(elapsed_seconds)
                if startup_status == 2:
                    print(
                        "ERROR: Gateway startup stalled before opening the API socket; collecting diagnostics.",
                        file=sys.stderr,
                    )
                    diagnose()
                    return 1
            else:
                stable = 0
            time.sleep(poll_interval_seconds)
            attempt += 1
        print(
            "ERROR: broker auth, Gateway API, or trading-permission readiness timed out before the API "
            f"socket became stably tradable ({restarts} forced restart(s) used); collecting diagnostics.",
            file=sys.stderr,
        )
        diagnose()
        return 1

    if timed("GCP project configuration", lambda: gcloud("config", "set", "project", project_id, timeout=60)) != 0:
        return 1

    if action == "status":
        return remote(f"sudo systemctl status ibgateway --no-pager --lines={log_lines} || true", timeout=180)
    if action == "restart":
        if repair_runtime() != 0:
            return 1
        return remote("sudo systemctl restart ibgateway", timeout=180)
    if action == "logs":
        return remote(f"sudo journalctl -u ibgateway -n {log_lines} --no-pager", timeout=180)
    if action == "app-logs":
        return remote(
            "echo '===== poma-cron.log (tail) ====='; "
            f"sudo tail -n {log_lines} /opt/poma/logs/poma-cron.log 2>/dev/null || echo '(missing)'; "
            "echo '===== poma-reconcile-cron.log (tail) ====='; "
            f"sudo tail -n {log_lines} /opt/poma/logs/poma-reconcile-cron.log 2>/dev/null || echo '(missing)'; "
            "echo '===== rebalance_state.json ====='; "
            "sudo cat /opt/poma/state/rebalance_state.json 2>/dev/null || echo '(missing)'",
            timeout=180,
        )
    if action == "clear-rebalance-state":
        return remote(
            "sudo install -d -o poma -g poma /opt/poma/state && "
            "sudo rm -f /opt/poma/state/rebalance_state.json && "
            "echo 'Cleared /opt/poma/state/rebalance_state.json; next eligible monitor run may rebalance again.'",
            timeout=180,
        )
    if action == "verify-socket":
        if repair_runtime() != 0:
            return 1
        remote("sudo systemctl restart ibgateway || true", timeout=180)
        return api_ready("paper", required=False)

    if action not in {"configure-paper", "configure-live"}:
        print(f"unknown action: {action}", file=sys.stderr)
        return 2

    login_id = env("BROKER_LOGIN_ID")
    login_secret = env("BROKER_LOGIN_VALUE")
    if repair_runtime() != 0:
        return 1
    mode = action.removeprefix("configure-")
    configure_input = f"{login_id}\n{login_secret}\n{mode}\n"
    if timed(
        "Configure IBC auth values",
        lambda: gcloud(
            "compute",
            "ssh",
            vm_name,
            *ssh_common,
            "--command",
            "sudo POMA_CONFIGURE_IBC_RESTART=0 poma-configure-ibc",
            timeout=180,
            input_text=configure_input,
        ),
    ) != 0:
        return 1
    if timed("Validate IBC configuration", lambda: remote(f"sudo poma-diagnose-ibgateway validate --mode {mode}", timeout=120)) != 0:
        return 1
    timed("Clear stale Gateway auth logs", lambda: remote("sudo poma-wait-ibgateway-2fa --truncate-logs-only", timeout=120))
    print("Force fresh ibgateway login after IBC configuration")
    # Fresh 2FA challenge wait is enforced for live configure only; paper skips it.
    if timed("Restart ibgateway after IBC configuration", lambda: remote("sudo systemctl restart ibgateway", timeout=240)) != 0:
        diagnose()
        return 1
    if mode == "paper":
        print("Paper Gateway configure will verify API and trading readiness directly.")
        return api_ready(mode, required=True)

    wait_command = (
        f"sudo poma-wait-ibgateway-2fa --log-lines 80 --timeout-seconds {twofa_timeout} "
        f"--poll-seconds {poll_seconds} --fail-no-progress-after {no_progress_after}"
    )
    if timed("Fresh live 2FA challenge wait", lambda: remote(wait_command, timeout=480)) != 0:
        print("No fresh IBKR mobile 2FA evidence appeared; refusing live configure success.", file=sys.stderr)
        diagnose()
        return 1
    return api_ready(mode, required=True)


if __name__ == "__main__":
    raise SystemExit(main())
