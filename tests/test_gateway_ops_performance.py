from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
GATEWAY_OPS_WORKFLOW = REPO_ROOT / ".github/workflows/ib-gateway-ops.yml"
DIAG_HELPER = REPO_ROOT / "ops/scripts/diagnose_ib_gateway_runtime.py"
ENSURE_HELPER = REPO_ROOT / "ops/scripts/ensure_ibgateway_service.sh"


def _workflow() -> str:
    return GATEWAY_OPS_WORKFLOW.read_text(encoding="utf-8")


def test_gateway_ops_records_timing_summary_for_expensive_steps() -> None:
    workflow = _workflow()

    for snippet in (
        "### IB Gateway Ops timing",
        "Total step time:",
        "timed \"GCP project configuration\"",
        "timed \"IAP SSH/runtime sentinel check\"",
        "timed \"Upload gateway helper scripts\"",
        "timed \"Runtime repair/install\"",
        "timed \"Validate IBC configuration\"",
        "timed \"Clear stale Gateway auth logs\"",
        "timed \"Force fresh ibgateway login after IBC configuration\"",
        "Fresh 2FA startup classification",
        "timed \"Real API handshake\"",
        "Collect gateway diagnostics",
    ):
        assert snippet in workflow


def test_gateway_runtime_repair_is_idempotent_and_fails_open() -> None:
    workflow = _workflow()

    for snippet in (
        "gateway_runtime_revision",
        "sha256sum",
        "/var/lib/poma/ib-gateway-runtime-revision",
        "ops/scripts/diagnose_ib_gateway_runtime.py",
        "ensure_ibgateway_service.sh",
        "poma-diagnose-ibgateway",
        "Gateway runtime helpers already current",
        "skipping repair/install",
        "Gateway runtime sentinel missing or stale; fail-open",
        "sudo tee '${gateway_runtime_sentinel}'",
        "test -x /usr/local/bin/poma-configure-ibc",
        "test -x /usr/local/bin/poma-diagnose-ibgateway",
        "systemctl cat ibgateway",
    ):
        assert snippet in workflow


def test_gateway_socket_poll_combines_socket_and_service_checks() -> None:
    workflow = _workflow()

    assert "poll_gateway_socket_once" in workflow
    assert "if nc -z 127.0.0.1 7497; then exit 0" in workflow
    assert "if ! systemctl is-active --quiet ibgateway; then exit 2" in workflow
    assert "Socket/service poll attempt" in workflow
    assert "Gateway API socket stability guard" in workflow


def test_gateway_socket_poll_is_errexit_safe() -> None:
    workflow = _workflow()

    assert "if timed \"Socket/service poll attempt ${attempt}\" poll_gateway_socket_once; then" in workflow
    assert "stable=$((stable + 1))" in workflow
    assert "Gateway startup classification" in workflow
    assert "set +e\n              timed \"Socket/service poll attempt" not in workflow


def test_gateway_startup_failure_prints_actionable_compact_diagnosis() -> None:
    workflow = _workflow()

    for snippet in (
        "startup_check_file=\"$(mktemp)\"",
        "print_compact_startup_diagnosis",
        "STARTUP_STAGE=$(compact_value STARTUP_STAGE",
        "STARTUP_ACTION=$(compact_value STARTUP_ACTION",
        "STARTUP_REASON=$(compact_value STARTUP_REASON",
        "NEXT_ACTION=Approve broker mobile authentication",
        "startup-check-missing-stage",
        "IB Gateway startup classification",
    ):
        assert snippet in workflow


def test_gateway_socket_requires_stable_guard_before_real_handshake() -> None:
    workflow = _workflow()
    wait_for_api = workflow.split("          wait_for_api_readiness() {", 1)[1].split(
        "\n          }",
        1,
    )[0]

    for snippet in (
        "local deadline=$((SECONDS + timeout_seconds)) attempt=0 elapsed=0 stable=0",
        "required_stable=2",
        "stable_socket_polls_required=${required_stable}",
        "Gateway API socket stability guard",
        "Real API handshake",
    ):
        assert snippet in wait_for_api

    assert wait_for_api.index("Gateway API socket stability guard") < wait_for_api.index(
        'timed "Real API handshake" verify_api_handshake'
    )


def test_gateway_configure_requires_fresh_2fa_before_api_handshake() -> None:
    workflow = _workflow()
    block = workflow.split("configure-paper|configure-live)", 1)[1].split(";;", 1)[0]

    for snippet in (
        "wait_for_fresh_2fa_challenge",
        "fresh_2fa_evidence",
        "configure_requires_fresh_2fa=true",
        "Gateway API socket opened before fresh 2FA evidence",
        "No fresh IBKR mobile 2FA evidence appeared",
        'wait_for_api_readiness "${mode}" 1 0',
    ):
        assert snippet in workflow

    assert 'wait_for_api_readiness "${mode}" 1 1' not in block
    assert "Treating configure as auth-pending" not in workflow
    assert block.index("wait_for_fresh_2fa_challenge") < block.index(
        'wait_for_api_readiness "${mode}" 1 0'
    )


def test_gateway_exit_writes_final_compact_diagnosis_to_job_log() -> None:
    workflow = _workflow()

    assert "Final compact Gateway diagnosis:" in workflow
    assert "print_compact_startup_diagnosis" in workflow
    assert "write_timing_summary" in workflow
    assert "trap 'cleanup_input_file; echo \"Final compact Gateway diagnosis:" in workflow


def test_gateway_ops_restarts_after_config_write_before_waiting() -> None:
    workflow = _workflow()
    block = workflow.split("configure-paper|configure-live)", 1)[1].split(";;", 1)[0]

    configure = "timed \"Configure IBC auth values\""
    validate = "timed \"Validate IBC configuration\""
    clear_logs = "timed \"Clear stale Gateway auth logs\""
    force_login = "timed \"Force fresh ibgateway login after IBC configuration\""
    fresh_2fa = "wait_for_fresh_2fa_challenge"
    wait = "wait_for_api_readiness"

    for snippet in (configure, validate, clear_logs, force_login, fresh_2fa, wait):
        assert snippet in block
    assert block.index(configure) < block.index(validate)
    assert block.index(validate) < block.index(clear_logs)
    assert block.index(clear_logs) < block.index(force_login)
    assert block.index(force_login) < block.index(fresh_2fa)
    assert block.index(fresh_2fa) < block.index(wait)
    assert "POMA_CONFIGURE_IBC_RESTART=0" in block
    assert "Pre-build poma Docker image" not in block


def test_gateway_ops_has_explicit_bounded_2fa_timeout() -> None:
    workflow = _workflow()

    assert "IB_GATEWAY_2FA_APPROVAL_TIMEOUT_SECONDS: 360" in workflow
    assert "Waiting up to ${timeout_seconds}s for broker auth and Gateway API readiness" in workflow
    assert "Waiting up to ${timeout_seconds}s for a fresh IBKR mobile 2FA challenge" in workflow
    assert "Broker auth or Gateway API readiness timed out" in workflow
    assert "No fresh IBKR mobile 2FA evidence appeared" in workflow
    assert "local deadline=$((SECONDS + timeout_seconds))" in workflow


def test_gateway_ops_preserves_authenticated_api_check_and_diagnostics() -> None:
    workflow = _workflow()
    helper = DIAG_HELPER.read_text(encoding="utf-8")

    for snippet in (
        "poma ibkr-check",
        "stable_socket_polls_required=${required_stable}",
        "Final compact Gateway diagnosis",
        "poma-diagnose-ibgateway validate --mode",
        "poma-diagnose-ibgateway progress",
        "poma-diagnose-ibgateway diagnose",
        "poma-diagnose-ibgateway startup-check",
    ):
        assert snippet in workflow
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

    assert "timeout-minutes: 25" in workflow
    assert "timeout --kill-after=30s" in workflow
    assert "IB_GATEWAY_2FA_APPROVAL_TIMEOUT_SECONDS: 360" in workflow
    assert "IB_GATEWAY_SOCKET_POLL_SECONDS: 5" in workflow
    assert "run_remote" in workflow


def test_api_handshake_remote_command_preserves_runtime_mode() -> None:
    workflow = _workflow()
    verify_api_handshake = workflow.split("          verify_api_handshake() {", 1)[1].split(
        "\n          }",
        1,
    )[0]

    assert "TRADING_MODE=${mode}" in verify_api_handshake
    assert "DATA_PROVIDER=fixture" in verify_api_handshake
    assert "poma ibkr-check" in verify_api_handshake
