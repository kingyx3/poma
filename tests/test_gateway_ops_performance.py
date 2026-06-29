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
    assert "IB_GATEWAY_2FA_APPROVAL_TIMEOUT_SECONDS: 360" in workflow
    assert "IB_GATEWAY_SOCKET_POLL_SECONDS: 5" in workflow
    assert "IB_GATEWAY_LOGIN_PROGRESS_GRACE_SECONDS: 200" in workflow
    assert "Resolve broker login secrets" in workflow
    assert "IBKR_LOGIN_ID_PAPER" in workflow
    assert "IBKR_LOGIN_SECRET_PAPER" in workflow
    assert "BROKER_LOGIN_ID" in workflow
    assert "BROKER_LOGIN_VALUE" in workflow


def test_gateway_runner_records_timing_summary_for_expensive_steps() -> None:
    runner = _runner()

    for snippet in (
        "TIMING {label}",
        "GCP project configuration",
        "IAP SSH/runtime sentinel check",
        "Upload gateway helper scripts",
        "Runtime repair/install",
        "Configure IBC auth values",
        "Validate IBC configuration",
        "Clear stale Gateway auth logs",
        "Force fresh ibgateway login after IBC configuration",
        "Fresh 2FA challenge wait",
        "Collect gateway diagnostics",
    ):
        assert snippet in runner


def test_gateway_runtime_repair_installs_all_helpers_idempotently() -> None:
    runner = _runner()

    for snippet in (
        "revision = helper_revision()",
        "helper_revision",
        "/var/lib/poma/ib-gateway-runtime-revision",
        "ops/scripts/diagnose_ib_gateway_runtime.py",
        "ops/scripts/wait_ib_gateway_2fa.py",
        "ensure_ibgateway_service.sh",
        "poma-diagnose-ibgateway",
        "poma-wait-ibgateway-2fa",
        "Gateway runtime helpers already current",
        "skipping repair/install",
        "Gateway runtime sentinel missing or stale; fail-open",
        "systemctl cat ibgateway",
    ):
        assert snippet in runner


def test_gateway_socket_poll_combines_socket_and_service_checks() -> None:
    runner = _runner()

    assert "nc -z 127.0.0.1 7497" in runner
    assert "systemctl is-active --quiet ibgateway" in runner
    assert "Gateway API socket stability guard" in runner
    assert "stable >= 2" in runner


def test_gateway_configure_requires_fresh_2fa_before_api_handshake() -> None:
    runner = _runner()

    assert "poma-wait-ibgateway-2fa --log-lines 80" in runner
    assert "Fresh 2FA challenge wait" in runner
    assert "No fresh IBKR mobile 2FA evidence appeared" in runner
    assert "poma ibkr-check" in runner
    assert runner.index("Fresh 2FA challenge wait") < runner.index("api_ready(mode, required=True)")


def test_gateway_runner_restarts_after_config_write_before_waiting() -> None:
    runner = _runner()

    configure = "Configure IBC auth values"
    validate = "Validate IBC configuration"
    clear_logs = "Clear stale Gateway auth logs"
    force_login = "Force fresh ibgateway login after IBC configuration"
    fresh_2fa = "Fresh 2FA challenge wait"

    for snippet in (configure, validate, clear_logs, force_login, fresh_2fa):
        assert snippet in runner
    assert runner.index(configure) < runner.index(validate)
    assert runner.index(validate) < runner.index(clear_logs)
    assert runner.index(clear_logs) < runner.index(force_login)
    assert runner.index(force_login) < runner.index(fresh_2fa)
    assert "POMA_CONFIGURE_IBC_RESTART=0" in runner


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


def test_gateway_ops_has_explicit_bounded_2fa_timeout() -> None:
    workflow = _workflow()
    runner = _runner()
    helper = WAIT_HELPER.read_text(encoding="utf-8")

    assert "IB_GATEWAY_2FA_APPROVAL_TIMEOUT_SECONDS: 360" in workflow
    assert "timeout_seconds=args.timeout_seconds" in helper
    assert "poll_seconds=args.poll_seconds" in helper
    assert "Broker auth or Gateway API readiness timed out" in runner
    assert "No fresh IBKR mobile 2FA evidence appeared" in helper


def test_gateway_ops_preserves_authenticated_api_check_and_diagnostics() -> None:
    runner = _runner()
    helper = DIAG_HELPER.read_text(encoding="utf-8")

    for snippet in (
        "poma ibkr-check",
        "poma-diagnose-ibgateway validate --mode",
        "poma-diagnose-ibgateway diagnose",
        "poma-diagnose-ibgateway startup-check",
    ):
        assert snippet in runner
    assert "ss" in helper
    for port in ("7497", "4001", "4002", "5900"):
        assert port in helper
    assert "Gateway/IBC likely has not reached the IBKR login/2FA stage" in helper
    assert "***" in helper


def test_gateway_runner_is_hardened_after_render() -> None:
    ensure = ENSURE_HELPER.read_text(encoding="utf-8")

    for snippet in (
        "poma-ibc-gateway-engine",
        "gatewaystart.sh -inline",
        "Gateway process or API listener detected",
        "refusing raw Gateway fallback",
        "require_command java",
        "MemoryMax",
        "gatewaystart-wrapper.log",
        "gatewaystart.sh exited before Java/Gateway stayed alive",
    ):
        assert snippet in ensure


def test_gateway_ops_keeps_bounded_timeouts() -> None:
    workflow = _workflow()
    runner = _runner()

    assert "timeout-minutes: 25" in workflow
    assert "IB_GATEWAY_2FA_APPROVAL_TIMEOUT_SECONDS: 360" in workflow
    assert "IB_GATEWAY_SOCKET_POLL_SECONDS: 5" in workflow
    assert "timeout=480" in runner
    assert "timeout=900" in runner


def test_api_handshake_remote_command_preserves_runtime_mode() -> None:
    runner = _runner()

    assert "TRADING_MODE=" in runner
    assert "DATA_PROVIDER=fixture" in runner
    assert "poma ibkr-check" in runner
