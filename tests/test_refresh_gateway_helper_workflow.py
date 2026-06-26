from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = REPO_ROOT / ".github/workflows/refresh-gateway-helper.yml"
SCRIPT = REPO_ROOT / "ops/scripts/refresh_gateway_helper.sh"


def test_refresh_gateway_helper_workflow_is_environment_scoped() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "deploy_environment:" in workflow
    assert "- dev" in workflow
    assert "- stg" in workflow
    assert "- prd" in workflow
    assert "environment: ${{ inputs.deploy_environment }}" in workflow
    assert "DEPLOY_ENVIRONMENT: ${{ inputs.deploy_environment }}" in workflow
    assert "bash ops/scripts/refresh_gateway_helper.sh" in workflow
    assert "google-github-actions/auth@v3" in workflow
    assert "google-github-actions/setup-gcloud@v3" in workflow


def test_refresh_gateway_helper_script_installs_helper() -> None:
    script = SCRIPT.read_text(encoding="utf-8")

    assert "install_ibc_config_helper.py" in script
    assert "poma-configure-ibc" in script
    assert "--tunnel-through-iap" in script
