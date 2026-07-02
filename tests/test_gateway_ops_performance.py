from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
GATEWAY_OPS_WORKFLOW = REPO_ROOT / ".github/workflows/ib-gateway-ops.yml"
GATEWAY_OPS_RUNNER = REPO_ROOT / "ops/scripts/run_gateway_ops_workflow.py"
DIAG_HELPER = REPO_ROOT / "ops/scripts/diagnose_ib_gateway_runtime.py"
ENSURE_HELPER = REPO_ROOT / "ops/scripts/ensure_ibgateway_service.sh"
WAIT_HELPER = REPO_ROOT / "ops/scripts/wait_ib_gateway_2fa.py"


def _workflow() -> str:
    return GATEWAY_OPS_WORKFLOW.read_text(encoding="utf-8")


def _runner() -> str:
    return GATEWAY_OPS_RUNNER.read_text(encoding="utf-8")


def test_gateway_ops_workflow_delegates_to_python_runner() -> None:
    workflow = _workflow()

    assert "python3 ops/scripts/run_gateway_ops_workflow.py" in workflow
    assert "IB_GATEWAY_2FA_APPROVAL_TIMEOUT_SECONDS: 600" in workflow
    assert "IB_GATEWAY_SOCKET_POLL_SECONDS: 5" in workflow
    assert "IB_GATEWAY_LOGIN_PROGRESS_GRACE_SECONDS: 540" in workflow
    assert "Resolve broker login secrets" in workflow


def test_gateway_runner_checks_trading_readiness_and_startup_progress() -> None:
    runner = _runner()

    for snippet in (
        "Paper Gateway configure will verify API and trading readiness directly.",
        "Gateway startup progress check",
        "Gateway startup stalled before opening the API socket; collecting diagnostics.",
        "poma-diagnose-ibgateway startup-check",
        "poma ibkr-check",
        "readiness timed out before the API",
        "TRADING_MODE=",
        "DATA_PROVIDER=fixture",
    ):
        assert snippet in runner


def test_gateway_readiness_uses_pulled_vm_image_and_bounded_restarts() -> None:
    runner = _runner()

    # Readiness what-if must reuse the deployed VM image (host-networked, pulled) instead of
    # rebuilding from source on the e2-micro or racing container-namespaced 127.0.0.1.
    assert "-f docker-compose.vm.yml" in runner
    assert "--env-file .compose.env" in runner
    assert "docker compose run --rm" not in runner
    # A forced trading-login restart must reset the per-login budget and be bounded, then fail fast
    # with an actionable read-only/permissions message instead of silently timing out.
    assert "max_trading_login_restarts = 2" in runner
    assert "login_started = time.monotonic()" in runner
    assert "'Read-Only API' disabled" in runner


def test_gateway_ops_logs_stay_concise_and_error_specific() -> None:
    runner = _runner()

    # gcloud's per-invocation chatter (ssh metadata updates, key propagation waits, IAP NumPy
    # warnings) repeats on every poll attempt and buries the actionable failure lines.
    assert "--verbosity=error" in runner
    assert "--no-user-output-enabled" in runner
    # The fixed gcloud ssh boilerplate is echoed compactly; only the remote command varies.
    assert "_echo_command" in runner
    assert '"+ " + " ".join(command)' in runner


def test_gateway_ops_supports_read_only_market_data_verification() -> None:
    workflow = _workflow()
    runner = _runner()

    assert "verify-market-data" in workflow
    verify_block = runner.split('if action == "verify-market-data":', 1)[1].split("if action not in", 1)[0]
    # The entitlement check must observe the running Gateway session as-is: a restart or
    # repair would force a fresh login and hide what the deployed session actually serves.
    assert "ibkr_check_command" in verify_block
    assert "systemctl restart" not in verify_block
    assert "repair_runtime()" not in verify_block


def test_gateway_runtime_repair_installs_helpers_idempotently() -> None:
    runner = _runner()

    for snippet in (
        "revision = helper_revision()",
        "/var/lib/poma/ib-gateway-runtime-revision",
        "poma-diagnose-ibgateway",
        "poma-wait-ibgateway-2fa",
        "Gateway runtime helpers already current",
        "Gateway runtime sentinel missing or stale; fail-open",
        "systemctl cat ibgateway",
    ):
        assert snippet in runner


def test_gateway_socket_poll_combines_socket_and_service_checks() -> None:
    runner = _runner()

    assert "nc -z 127.0.0.1 7497" in runner
    assert "systemctl is-active --quiet ibgateway" in runner
    assert "stable >= 2" in runner


def test_gateway_wait_helper_runs_locally_on_vm_and_prints_progress() -> None:
    helper = WAIT_HELPER.read_text(encoding="utf-8")

    for snippet in (
        "configure_requires_fresh_2fa=true",
        "Fresh 2FA startup classification",
        "Fresh IBKR mobile 2FA/login-auth evidence detected",
        "Gateway API socket opened before fresh IBKR mobile 2FA evidence",
        "No fresh IBKR mobile 2FA evidence appeared after configure",
        "--truncate-logs-only",
        "TWO_FA_HINTS",
    ):
        assert snippet in helper


def test_gateway_startup_check_prints_visible_failure_before_returning_status_2() -> None:
    helper = DIAG_HELPER.read_text(encoding="utf-8")

    assert "print_visible_startup_failure" in helper
    assert "::endgroup::" in helper
    assert "Visible gateway startup diagnostic" in helper
    assert "VISIBLE_STARTUP_CHECK_STATUS=failed" in helper
    assert "VISIBLE_STARTUP_STAGE=" in helper
    assert "VISIBLE_STARTUP_REASON=" in helper
    assert "return 2" in helper


def test_gateway_ops_preserves_diagnostics_and_hardening() -> None:
    runner = _runner()
    helper = DIAG_HELPER.read_text(encoding="utf-8")
    ensure = ENSURE_HELPER.read_text(encoding="utf-8")

    assert "poma-diagnose-ibgateway diagnose" in runner
    assert "ss" in helper
    for port in ("7497", "4001", "4002", "5900"):
        assert port in helper
    assert "Gateway/IBC likely has not reached the IBKR login/2FA stage" in helper
    assert "poma-ibc-gateway-engine" in ensure
    assert "refusing raw Gateway fallback" in ensure
    assert "gatewaystart.sh exited before Java/Gateway stayed alive" in ensure


def test_gateway_ops_emits_compact_failure_summary_before_verbose_diagnostics() -> None:
    runner = _runner()

    for snippet in (
        "GATEWAY_FAILURE_STAGE=",
        "GATEWAY_FAILURE_REASON=",
        "GATEWAY_NEXT_ACTION=",
        "::error title=",
        "Gateway configure failure",
        "Collect post-failure diagnostics",
        "Diagnostics collected successfully; original failure remains",
        "Full redacted diagnostics remain",
    ):
        assert snippet in runner
    assert "Collect gateway diagnostics" not in runner


def test_gateway_ops_keeps_bounded_timeouts() -> None:
    workflow = _workflow()
    runner = _runner()

    assert "timeout-minutes: 30" in workflow
    assert "IB_GATEWAY_2FA_APPROVAL_TIMEOUT_SECONDS: 600" in workflow
    assert "IB_GATEWAY_LOGIN_PROGRESS_GRACE_SECONDS: 540" in workflow
    assert "IB_GATEWAY_SOCKET_POLL_SECONDS: 5" in workflow
    assert "timeout=480" in runner
    assert "timeout=900" in runner
