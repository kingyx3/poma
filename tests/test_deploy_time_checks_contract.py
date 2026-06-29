from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_gateway_configure_actions_require_2fa_then_readiness_wait() -> None:
    workflow = (REPO_ROOT / ".github/workflows/ib-gateway-ops.yml").read_text(encoding="utf-8")

    configure_block = workflow.split("configure-paper|configure-live)", 1)[1].split(";;", 1)[0]

    assert "configure-paper|configure-live)" in workflow
    assert "Clear stale Gateway auth logs" in configure_block
    assert "ibgateway login after IBC configuration" in configure_block
    assert 'wait_for_fresh_2fa_challenge "${mode}"' in configure_block
    assert 'wait_for_api_readiness "${mode}" 1 0' in configure_block
    assert 'wait_for_api_readiness "${mode}" 1 1' not in configure_block
    assert configure_block.index("wait_for_fresh_2fa_challenge") < configure_block.index(
        "wait_for_api_readiness"
    )
    assert "poma ibkr-check" in workflow


def test_gateway_verify_socket_is_the_strict_readiness_gate() -> None:
    workflow = (REPO_ROOT / ".github/workflows/ib-gateway-ops.yml").read_text(encoding="utf-8")

    verify_block = workflow.split("verify-socket)", 1)[1].split(";;", 1)[0]
    assert "wait_for_api_readiness paper 0 0" in verify_block
    assert "poma ibkr-check" in workflow
