#!/usr/bin/env python3
# ruff: noqa: E501, F401, F841
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import tarfile
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
HELPER_ARCHIVE_NAME = "poma-gateway-helpers.tar.gz"
NON_RETRIABLE_IBKR_MARKET_DATA_ERRORS = (
    ("10089", "requested market data requires additional subscription"),
    ("354", "requested market data is not subscribed"),
    ("10197", "no market data during competing live session"),
)
NON_RETRIABLE_IBKR_ACCOUNT_ERRORS = (
    ("10141", "paper trading disclaimer must first be accepted"),
)


def env(name: str, default: str | None = None) -> str:
    value = os.environ.get(name, default)
    if value is None or value == "":
        raise SystemExit(f"missing required env: {name}")
    return value


def _github_escape(value: str) -> str:
    return value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def _emit_github_error(title: str, message: str) -> None:
    print(f"::error title={_github_escape(title)}::{_github_escape(message)}", file=sys.stderr)


def _append_step_summary(markdown: str) -> None:
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return
    with Path(summary_path).open("a", encoding="utf-8") as handle:
        handle.write(markdown.rstrip())
        handle.write("\n")


def _echo_command(command: list[str]) -> str:
    """Compact command echo: the fixed gcloud ssh boilerplate repeated on every poll buries the
    lines that matter, so print only the part that varies (the target VM and remote command)."""
    if command[:3] == ["gcloud", "compute", "ssh"] and "--command" in command:
        remote_command = command[command.index("--command") + 1]
        return f"+ [ssh {command[3]}] {remote_command}"
    return "+ " + " ".join(command)


def run(command: list[str], *, timeout: int = 180, input_text: str | None = None) -> int:
    print(_echo_command(command), flush=True)
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


def run_capture(command: list[str], *, timeout: int = 180) -> tuple[int, str]:
    print(_echo_command(command), flush=True)
    def as_text(part) -> str:
        if isinstance(part, bytes):
            return part.decode(errors="replace")
        return part or ""

    try:
        completed = subprocess.run(
            command,
            check=False,
            text=True,
            timeout=timeout,
            capture_output=True,
        )
    except subprocess.TimeoutExpired as exc:
        output = "".join(as_text(part) for part in (exc.stdout, exc.stderr))
        if output:
            print(output, end="" if output.endswith("\n") else "\n")
        print(f"command timed out after {timeout}s: {' '.join(command)}", file=sys.stderr)
        return 124, output
    output = completed.stdout + completed.stderr
    if output:
        print(output, end="" if output.endswith("\n") else "\n")
    return completed.returncode, output


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


def build_helper_archive() -> Path:
    archive_path = Path(tempfile.gettempdir()) / HELPER_ARCHIVE_NAME
    archive_path.unlink(missing_ok=True)
    with tarfile.open(archive_path, "w:gz") as archive:
        for script in HELPER_SCRIPTS:
            path = Path(script)
            archive.add(path, arcname=path.name)
    return archive_path


def classify_non_retriable_ibkr_check(output: str) -> tuple[str, str] | None:
    lower_output = output.lower()
    if any(code in lower_output and phrase in lower_output for code, phrase in NON_RETRIABLE_IBKR_MARKET_DATA_ERRORS):
        return (
            "poma ibkr-check failed with an IBKR market-data entitlement/session error.",
            "Fix the IBKR market-data/API entitlement or close the competing live session, then rerun Gateway configure.",
        )
    if any(code in lower_output and phrase in lower_output for code, phrase in NON_RETRIABLE_IBKR_ACCOUNT_ERRORS):
        return (
            "poma ibkr-check failed because the IBKR paper account requires manual disclaimer acceptance before API connections.",
            "Log into the IBKR paper account and accept the paper trading/API disclaimer, then rerun Gateway configure.",
        )
    return None


def main() -> int:
    deploy_environment = env("DEPLOY_ENVIRONMENT")
    action = env("INPUT_ACTION")
    log_lines = env("LOG_LINES", "200")
    project_id = env("GCP_PROJECT_ID")
    zone = env("GCP_ZONE")
    vm_name = env("GCP_VM_NAME")
    twofa_timeout = env("IB_GATEWAY_2FA_APPROVAL_TIMEOUT_SECONDS", "300")
    poll_seconds = env("IB_GATEWAY_SOCKET_POLL_SECONDS", "5")
    no_progress_after = env("IB_GATEWAY_LOGIN_PROGRESS_GRACE_SECONDS", "180")
    max_trading_login_restarts = int(env("IB_GATEWAY_MAX_TRADING_LOGIN_RESTARTS", "1"))
    ibkr_check_timeout_seconds = int(env("IB_GATEWAY_IBKR_CHECK_TIMEOUT_SECONDS", "300"))
    runtime_repair_timeout_seconds = int(env("IB_GATEWAY_RUNTIME_REPAIR_TIMEOUT_SECONDS", "780"))
    # --verbosity=error and --no-user-output-enabled silence gcloud's per-invocation chatter
    # ("Updating instance ssh metadata...", SSH key propagation waits, IAP NumPy warnings) that
    # otherwise repeats on every poll attempt; remote command output is unaffected (it comes from
    # the ssh subprocess, not gcloud's output framework), and real gcloud errors still print.
    ssh_common = [
        "--zone",
        zone,
        "--tunnel-through-iap",
        "--ssh-key-expire-after=15m",
        "--quiet",
        "--verbosity=error",
        "--no-user-output-enabled",
    ]
    sentinel = "/var/lib/poma/ib-gateway-runtime-revision"
    revision = helper_revision()

    def gcloud(*args: str, timeout: int = 180, input_text: str | None = None) -> int:
        return run(["gcloud", *args], timeout=timeout, input_text=input_text)

    def remote(command: str, timeout: int = 180) -> int:
        return gcloud("compute", "ssh", vm_name, *ssh_common, "--command", command, timeout=timeout)

    def remote_capture(command: str, timeout: int = 180) -> tuple[int, str]:
        return run_capture(["gcloud", "compute", "ssh", vm_name, *ssh_common, "--command", command], timeout=timeout)

    def record_failure(stage: str, reason: str, next_action: str) -> None:
        summary = (
            "===== Gateway failure summary =====\n"
            f"GATEWAY_FAILURE_STAGE={stage}\n"
            f"GATEWAY_FAILURE_REASON={reason}\n"
            f"GATEWAY_NEXT_ACTION={next_action}"
        )
        print(summary, file=sys.stderr)
        _emit_github_error("Gateway configure failed", f"{stage}: {reason} Next action: {next_action}")
        _append_step_summary(
            "\n".join(
                [
                    "## Gateway configure failure",
                    "",
                    f"- **Environment:** `{deploy_environment}`",
                    f"- **Action:** `{action}`",
                    f"- **Stage:** `{stage}`",
                    f"- **Reason:** {reason}",
                    f"- **Next action:** {next_action}",
                    "",
                    "Full redacted diagnostics remain in the workflow log groups.",
                ]
            )
        )

    def repair_runtime() -> int:
        check = (
            f"test -f '{sentinel}' && [ \"$(cat '{sentinel}')\" = '{revision}' ] && "
            "test -x /usr/local/bin/poma-configure-ibc && "
            "test -x /usr/local/bin/poma-run-ib-gateway && "
            "test -x /usr/local/bin/poma-diagnose-ibgateway && "
            "test -x /usr/local/bin/poma-wait-ibgateway-2fa && "
            "systemctl cat ibgateway >/dev/null"
        )
        if timed("IAP SSH/runtime sentinel check", lambda: remote(check, timeout=45)) == 0:
            print(f"Gateway runtime helpers already current ({revision}); skipping repair/install.")
            return 0
        print("Gateway runtime sentinel missing or stale; fail-open by running repair/install.")
        helper_archive = build_helper_archive()
        try:
            upload = gcloud(
                "compute",
                "scp",
                str(helper_archive),
                f"{vm_name}:/tmp/{HELPER_ARCHIVE_NAME}",
                *ssh_common,
                timeout=240,
            )
        finally:
            helper_archive.unlink(missing_ok=True)
        print(f"TIMING Upload gateway helper bundle: status={upload}")
        if upload != 0:
            return upload
        timed(
            "Gateway runtime preflight",
            lambda: remote(
                "if test -x /opt/ibgateway/ibgateway || find /opt/ibgateway -type d -name jars -print -quit 2>/dev/null | grep -q .; then "
                "echo 'Gateway runtime artifacts already present; helper/service repair should be quick.'; "
                "else echo 'Fresh Gateway runtime install path; first install on e2-micro may take several minutes.'; fi",
                timeout=60,
            ),
        )
        install = (
            f"sudo tar -xzf /tmp/{HELPER_ARCHIVE_NAME} -C /tmp && "
            f"sudo rm -f /tmp/{HELPER_ARCHIVE_NAME} && "
            "sudo install -m 755 /tmp/diagnose_ib_gateway_runtime.py /usr/local/bin/poma-diagnose-ibgateway && "
            "sudo install -m 755 /tmp/wait_ib_gateway_2fa.py /usr/local/bin/poma-wait-ibgateway-2fa && "
            "sudo python3 /tmp/repair_ib_gateway_runtime.py && "
            "sudo python3 /tmp/install_ibc_config_helper.py && "
            "sudo sh /tmp/ensure_ibgateway_service.sh && "
            "sudo install -d -m 755 /var/lib/poma && "
            f"printf '%s\n' '{revision}' | sudo tee '{sentinel}' >/dev/null"
        )
        status = timed("Runtime repair/install", lambda: remote(install, timeout=runtime_repair_timeout_seconds))
        if status == 0:
            return 0
        record_failure(
            "runtime-repair",
            f"Gateway runtime repair/install failed or exceeded {runtime_repair_timeout_seconds}s.",
            "Inspect the runtime diagnostics below. On a freshly replaced e2-micro, rerun after the first install settles only if diagnostics show the installer completed late.",
        )
        timed(
            "Collect runtime repair diagnostics",
            lambda: remote(
                "echo '===== runtime processes ====='; "
                "ps -eo pid,ppid,stat,etimes,comm,args | grep -E 'apt|dpkg|ibgateway|install4j|java|unzip|tar|python3' | grep -v grep || true; "
                "echo '===== disk ====='; df -h / /opt /tmp 2>/dev/null || df -h; "
                "echo '===== gateway artifact probe ====='; "
                "find /opt/ibgateway -maxdepth 4 \\( -name ibgateway -o -name jars \\) -print 2>/dev/null | head -50 || true; "
                "echo '===== ibc artifact probe ====='; "
                "find /opt/ibc -maxdepth 2 -type f \\( -name gatewaystart.sh -o -name config.ini \\) -print 2>/dev/null || true; "
                "if command -v poma-diagnose-ibgateway >/dev/null 2>&1; then sudo poma-diagnose-ibgateway diagnose --log-lines 80 || true; "
                "elif test -f /tmp/diagnose_ib_gateway_runtime.py; then sudo python3 /tmp/diagnose_ib_gateway_runtime.py diagnose --log-lines 80 || true; fi",
                timeout=240,
            ),
        )
        return status

    def diagnose(stage: str, reason: str, next_action: str) -> None:
        record_failure(stage, reason, next_action)
        command = (
            f"sudo poma-diagnose-ibgateway startup-check --log-lines 80 --elapsed-seconds {twofa_timeout} "
            f"--fail-no-progress-after {no_progress_after} || true; "
            "sudo poma-diagnose-ibgateway progress --log-lines 80 || true; "
            f"sudo poma-diagnose-ibgateway diagnose --log-lines {log_lines}"
        )
        timed("Collect post-failure diagnostics", lambda: remote(command, timeout=240))
        print(f"Diagnostics collected successfully; original failure remains: {stage} - {reason}")

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

    def wait_for_stable_api_socket(login_attempt: int, timeout_seconds: int) -> int:
        command = (
            f"started=\"$(date +%s)\"; deadline=$((started + {timeout_seconds})); "
            "stable=0; poll_attempt=1; progress_checked=0; "
            "echo 'Waiting on VM for two stable Gateway API socket polls.'; "
            "while [ \"$(date +%s)\" -lt \"${deadline}\" ]; do "
            "if nc -z 127.0.0.1 7497; then "
            "stable=$((stable + 1)); echo \"socket poll ${poll_attempt}: open (stable=${stable})\"; "
            "if [ \"${stable}\" -ge 2 ]; then exit 0; fi; "
            "else "
            "stable=0; "
            "if ! systemctl is-active --quiet ibgateway; then "
            "echo \"socket poll ${poll_attempt}: ibgateway service not active yet\"; "
            "else echo \"socket poll ${poll_attempt}: waiting for API socket\"; fi; "
            "fi; "
            "elapsed=$(( $(date +%s) - started )); "
            f"if [ \"${{elapsed}}\" -ge {no_progress_after} ] && [ \"${{progress_checked}}\" -eq 0 ]; then "
            "echo 'Running VM-local Gateway startup progress check'; "
            f"sudo poma-diagnose-ibgateway startup-check --log-lines 80 --elapsed-seconds \"${{elapsed}}\" --fail-no-progress-after {no_progress_after}; "
            "progress_status=$?; "
            "if [ \"${progress_status}\" -eq 2 ]; then exit 2; fi; "
            "progress_checked=1; "
            "fi; "
            f"sleep {int(poll_seconds)}; "
            "poll_attempt=$((poll_attempt + 1)); "
            "done; "
            "exit 1"
        )
        return timed(
            f"VM-local socket/service wait attempt {login_attempt}",
            lambda: remote(command, timeout=timeout_seconds + 45),
        )

    def ibkr_check_command(mode: str, required: bool) -> str:
        # Run the readiness check against the deployed VM image (docker-compose.vm.yml,
        # host-networked) so it reuses the pulled image instead of building from source on
        # the e2-micro and so 127.0.0.1:7497 reaches the host Gateway.
        return (
            "if ! test -f /opt/poma/docker-compose.vm.yml; then "
            + (
                "echo 'POMA app not deployed at /opt/poma (missing docker-compose.vm.yml)' >&2; exit 1; "
                if required
                else "echo 'POMA app not deployed at /opt/poma; skipping.'; exit 0; "
            )
            + "fi; sudo -u poma bash -lc 'cd /opt/poma && docker compose --env-file .compose.env -f docker-compose.vm.yml run --rm -e TRADING_MODE="
            + mode
            + " -e DATA_PROVIDER=fixture poma ibkr-check'"
        )

    def api_ready(mode: str, required: bool) -> int:
        timeout_seconds = int(twofa_timeout)
        started = time.monotonic()
        # Overall safety cap so bounded per-login restarts can never overrun the job timeout.
        hard_deadline = started + (max_trading_login_restarts + 1) * timeout_seconds
        attempt = 1
        restarts = 0
        print(
            f"Waiting up to {timeout_seconds}s per login attempt for broker auth and Gateway API trading readiness "
            f"({max_trading_login_restarts} forced restart(s) allowed)."
        )
        while True:
            remaining = int(hard_deadline - time.monotonic())
            if remaining <= 0:
                break
            socket_budget = min(timeout_seconds, remaining)
            socket_status = wait_for_stable_api_socket(attempt, socket_budget)
            if socket_status == 0:
                check_status, check_output = remote_capture(ibkr_check_command(mode, required), timeout=ibkr_check_timeout_seconds)
                if check_status == 0:
                    print("IBKR API handshake, trading preview, and market-data readiness check succeeded; Gateway is ready to submit orders.")
                    return 0
                non_retriable = classify_non_retriable_ibkr_check(check_output)
                if non_retriable is not None:
                    reason, next_action = non_retriable
                    print(f"ERROR: {reason} Not forcing a fresh Gateway login because the captured IBKR error is not fixed by restart.", file=sys.stderr)
                    diagnose("ibkr-check", reason, next_action)
                    return 1
                if restarts >= max_trading_login_restarts:
                    reason = (
                        "IBKR API socket is open, but poma ibkr-check still fails after "
                        f"{restarts} forced fresh-login restart(s)."
                    )
                    print(
                        "ERROR: "
                        + reason
                        + " Verify the account's API settings have 'Read-Only API' disabled and that Trading and Market Data subscriptions are active for the "
                        + mode
                        + " account.",
                        file=sys.stderr,
                    )
                    diagnose(
                        "ibkr-check",
                        reason,
                        "Read the ibkr fail line immediately above this summary. If it says unreachable/TimeoutError with the socket open, rerun after the VM settles; if it cites IBKR market-data/trading permissions, fix the IBKR account setting.",
                    )
                    return 1
                restarts += 1
                restart_gateway_for_trading_login(
                    "IBKR API socket opened but trading readiness failed. This usually means Gateway "
                    "logged in read-only / without Trading/Market-Data permissions. Forcing a fresh "
                    f"trading-enabled login (restart {restarts}/{max_trading_login_restarts})."
                )
                attempt += 1
                continue
            if socket_status == 2:
                print(
                    "ERROR: Gateway startup stalled before opening the API socket; collecting diagnostics.",
                    file=sys.stderr,
                )
                diagnose(
                    "gateway-startup",
                    "Gateway startup stalled before opening the API socket.",
                    "Inspect the visible startup diagnostic and recent IBC/Gateway log hints in the diagnostics group.",
                )
                return 1
            break
        reason = (
            "Broker auth, Gateway API, or trading-permission readiness timed out before the API "
            f"socket became stably tradable ({restarts} forced restart(s) used)."
        )
        print(f"ERROR: {reason} Collecting diagnostics.", file=sys.stderr)
        diagnose(
            "readiness-timeout",
            reason,
            "Check whether Gateway reached login/2FA, whether port 7497 opened, and whether the VM was CPU/memory constrained during poma ibkr-check.",
        )
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
            "echo '===== cron service status ====='; "
            "sudo systemctl status cron --no-pager --lines=10 || true; "
            "echo '===== poma crontab ====='; "
            "sudo crontab -l -u poma 2>&1 || echo '(no crontab installed for poma)'; "
            "echo '===== poma docker group membership ====='; "
            "getent group docker || echo '(no docker group)'; "
            "echo '===== /opt/poma/logs directory ====='; "
            "sudo ls -la /opt/poma/logs 2>&1 || echo '(missing)'; "
            "echo '===== poma-cron.log (tail) ====='; "
            f"sudo tail -n {log_lines} /opt/poma/logs/poma-cron.log 2>/dev/null || echo '(missing)'; "
            "echo '===== poma-reconcile-cron.log (tail) ====='; "
            f"sudo tail -n {log_lines} /opt/poma/logs/poma-reconcile-cron.log 2>/dev/null || echo '(missing)'; "
            "echo '===== rebalance_state.json ====='; "
            "sudo cat /opt/poma/state/rebalance_state.json 2>/dev/null || echo '(missing)'",
            timeout=180,
        )
    if action == "fix-app-docker-perms":
        return remote(
            # poma is intentionally created non-unique on uid/gid 1000, shared with the cloud
            # image's default "ubuntu" account (see infra/gcp-free-tier/startup.sh). crontab -u
            # poma and the cron daemon itself both resolve that shared uid back to "ubuntu" for
            # ownership/session purposes, so the crontab actually runs as ubuntu, not poma -- add
            # both names to the docker group so the fix holds regardless of which one a given tool
            # resolves the shared uid to.
            "sudo usermod -aG docker poma && "
            "sudo usermod -aG docker ubuntu && "
            "sudo systemctl restart cron && "
            "getent group docker && "
            "echo 'poma and ubuntu added to the docker group and cron restarted; "
            "the next monitor/reconcile-orders tick should reach the Docker API.'",
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
    if action == "verify-market-data":
        # Read-only market data entitlement verification: no repair, no restart. Runs poma
        # ibkr-check against the *currently running* Gateway session, so a green run proves the
        # account is genuinely serving entitled quotes (live during market hours, frozen after
        # hours; REQUIRE_LIVE_EXECUTION_QUOTES in the deployed .env decides how strict that is).
        return timed(
            "Market data entitlement check (poma ibkr-check)",
            lambda: remote(ibkr_check_command("paper", required=True), timeout=ibkr_check_timeout_seconds),
        )

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
        diagnose(
            "gateway-restart",
            "ibgateway service restart failed after IBC configuration.",
            "Inspect systemctl status and journalctl in the diagnostics group, then rerun configure after the service is repaired.",
        )
        return 1
    if mode == "paper":
        print("Paper Gateway configure will verify API and trading readiness directly.")
        return api_ready(mode, required=True)

    wait_command = (
        f"sudo poma-wait-ibgateway-2fa --log-lines 80 --timeout-seconds {twofa_timeout} "
        f"--poll-seconds {poll_seconds} --fail-no-progress-after {no_progress_after}"
    )
    if timed("Fresh live 2FA challenge wait", lambda: remote(wait_command, timeout=int(twofa_timeout) + 60)) != 0:
        print("No fresh IBKR mobile 2FA evidence appeared; refusing live configure success.", file=sys.stderr)
        diagnose(
            "live-2fa",
            "Fresh live 2FA approval evidence was not observed before the timeout.",
            "Approve the IBKR Mobile prompt and rerun configure-live. If no prompt appears, inspect the IBC login-stage diagnostics.",
        )
        return 1
    return api_ready(mode, required=True)


if __name__ == "__main__":
    raise SystemExit(main())
