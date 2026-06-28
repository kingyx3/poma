from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_gateway_configure_actions_install_config_and_run_readiness_wait() -> None:
    workflow = (REPO_ROOT / ".github/workflows/ib-gateway-ops.yml").read_text(encoding="utf-8")

    assert "configure-paper|configure-live)" in workflow
    assert 'wait_for_api_readiness "${mode}" 1 1' in workflow
    assert "poma ibkr-check" in workflow
    assert "verify-socket before paper/live trading" in workflow


def test_gateway_verify_socket_is_the_strict_readiness_gate() -> None:
    workflow = (REPO_ROOT / ".github/workflows/ib-gateway-ops.yml").read_text(encoding="utf-8")

    verify_block = workflow.split("verify-socket)", 1)[1].split(";;", 1)[0]
    assert "wait_for_api_readiness paper 0 0" in verify_block
    assert "poma ibkr-check" in workflow
