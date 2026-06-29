from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_gateway_configure_actions_require_2fa_then_readiness_wait() -> None:
    workflow = (REPO_ROOT / ".github/workflows/ib-gateway-ops.yml").read_text(encoding="utf-8")
    runner = (REPO_ROOT / "ops/scripts/run_gateway_ops_workflow.py").read_text(encoding="utf-8")

    assert "python3 ops/scripts/run_gateway_ops_workflow.py" in workflow
    assert "configure-paper" in workflow
    assert "configure-live" in workflow
    assert "Clear stale Gateway auth logs" in runner
    assert "Force fresh ibgateway login after IBC configuration" in runner
    assert "Fresh 2FA challenge wait" in runner
    assert "api_ready(mode, required=True)" in runner
    assert runner.index("Fresh 2FA challenge wait") < runner.index("api_ready(mode, required=True)")
    assert "poma ibkr-check" in runner


def test_gateway_verify_socket_is_the_strict_readiness_gate() -> None:
    workflow = (REPO_ROOT / ".github/workflows/ib-gateway-ops.yml").read_text(encoding="utf-8")
    runner = (REPO_ROOT / "ops/scripts/run_gateway_ops_workflow.py").read_text(encoding="utf-8")

    assert "python3 ops/scripts/run_gateway_ops_workflow.py" in workflow
    assert "verify-socket" in workflow
    assert 'if action == "verify-socket"' in runner
    assert 'return api_ready("paper", required=False)' in runner
    assert "poma ibkr-check" in runner
