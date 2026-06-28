from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_gateway_configure_actions_are_the_deploy_time_readiness_gate() -> None:
    workflow = (REPO_ROOT / '.github/workflows/ib-gateway-ops.yml').read_text(encoding='utf-8')

    assert 'configure-paper|configure-live)' in workflow
    assert 'verify_socket "${mode}" 1' in workflow
    assert 'poma ibkr-check' in workflow
    assert "real ib_insync connect" in workflow
