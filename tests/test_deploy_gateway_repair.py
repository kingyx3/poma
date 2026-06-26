from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEPLOY_WORKFLOW = REPO_ROOT / ".github/workflows/deploy-gcp-vm.yml"


def test_deploy_repairs_gateway_runtime_after_upload() -> None:
    workflow = DEPLOY_WORKFLOW.read_text(encoding="utf-8")

    assert "sudo python3 ops/scripts/install_ibc_config_helper.py" in workflow
    assert "sudo sh ops/scripts/ensure_ibgateway_service.sh" in workflow
