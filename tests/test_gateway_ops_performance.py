from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
GATEWAY_OPS_WORKFLOW = REPO_ROOT / ".github/workflows/ib-gateway-ops.yml"


def test_gateway_ops_records_timing_summary_for_expensive_steps() -> None:
    workflow = GATEWAY_OPS_WORKFLOW.read_text(encoding="utf-8")

    expected = (
        "### IB Gateway Ops timing",
        "Slowest step:",
        "Total step time:",
        "timed \"GCP project configuration\"",
        "timed \"IAP SSH/runtime sentinel check\"",
        "timed \"Upload gateway helper scripts\"",
        "timed \"Runtime repair/install\"",
        "timed \"Restart ibgateway\"",
        "timed \"Configure IBC credentials\"",
        "timed \"Real API handshake\"",
        "timed \"Collect gateway diagnostics\"",
    )
    for snippet in expected:
        assert snippet in workflow


def test_gateway_runtime_repair_is_idempotent_and_fails_open() -> None:
    workflow = GATEWAY_OPS_WORKFLOW.read_text(encoding="utf-8")

    assert "gateway_runtime_revision" in workflow
    assert "sha256sum" in workflow
    assert "/var/lib/poma/ib-gateway-runtime-revision" in workflow
    assert "Gateway runtime helpers already current" in workflow
    assert "skipping repair/install" in workflow
    assert "Gateway runtime sentinel missing or stale; fail-open" in workflow
    assert "sudo tee '${gateway_runtime_sentinel}'" in workflow
    assert "test -x /usr/local/bin/poma-configure-ibc" in workflow
    assert "systemctl cat ibgateway" in workflow


def test_gateway_socket_poll_combines_socket_and_service_checks() -> None:
    workflow = GATEWAY_OPS_WORKFLOW.read_text(encoding="utf-8")

    assert "poll_gateway_socket_once" in workflow
    assert "if nc -z 127.0.0.1 7497; then exit 0" in workflow
    assert "if ! systemctl is-active --quiet ibgateway; then exit 2" in workflow
    assert "Socket/service poll attempt" in workflow
    assert "IB Gateway service stopped before the API socket became reachable" in workflow


def test_gateway_socket_poll_is_errexit_safe() -> None:
    workflow = GATEWAY_OPS_WORKFLOW.read_text(encoding="utf-8")

    assert "if timed \"Socket/service poll attempt ${attempt}\" poll_gateway_socket_once; then" in workflow
    assert "status=0" in workflow
    assert "status=\"$?\"" in workflow
    assert "would otherwise abort the polling loop before the 5-minute deadline" in workflow
    assert "set +e\n              timed \"Socket/service poll attempt" not in workflow


def test_gateway_ops_has_explicit_five_minute_2fa_timeout() -> None:
    workflow = GATEWAY_OPS_WORKFLOW.read_text(encoding="utf-8")

    assert "IB_GATEWAY_2FA_APPROVAL_TIMEOUT_SECONDS: 300" in workflow
    assert "Waiting up to ${timeout_seconds}s (5 minutes) for IBKR 2FA approval" in workflow
    assert "IBKR 2FA approval or Gateway API readiness timed out" in workflow
    assert "Waiting for IBKR 2FA approval / Gateway API socket" in workflow
    assert "local deadline=$((SECONDS + timeout_seconds))" in workflow


def test_gateway_ops_preserves_authenticated_api_check_and_log_redaction() -> None:
    workflow = GATEWAY_OPS_WORKFLOW.read_text(encoding="utf-8")

    assert "poma ibkr-check" in workflow
    assert "real ib_insync connect" in workflow
    assert "GitHub Environment secrets are required" in workflow
    assert "shred -u" in workflow
    assert "IbPassword|TWSPASSWORD|Password|password" in workflow
    assert "IbLoginId|TWSUSERID" in workflow


def test_gateway_ops_keeps_bounded_timeouts() -> None:
    workflow = GATEWAY_OPS_WORKFLOW.read_text(encoding="utf-8")

    assert "timeout-minutes: 25" in workflow
    assert "timeout --kill-after=30s" in workflow
    assert "IB_GATEWAY_2FA_APPROVAL_TIMEOUT_SECONDS: 300" in workflow
    assert "IB_GATEWAY_SOCKET_POLL_SECONDS: 5" in workflow
    assert "run_remote" in workflow
    assert "timed out. Check IAP/SSH reachability" in workflow


def test_gateway_ops_loops_have_deadlines_or_bounded_attempts() -> None:
    workflow = GATEWAY_OPS_WORKFLOW.read_text(encoding="utf-8")

    assert "while [ \"${SECONDS}\" -lt \"${deadline}\" ]; do" in workflow
    assert "sleep \"${poll_seconds}\"" in workflow
    assert "exit 1" in workflow
    assert "diagnose_gateway_failure" in workflow
