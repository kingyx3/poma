from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP_WORKFLOW = REPO_ROOT / ".github/workflows/bootstrap-gcp-wif.yml"
DEPLOY_WORKFLOW = REPO_ROOT / ".github/workflows/deploy-gcp-vm.yml"


REQUIRED_ENVIRONMENT_SNIPPETS = (
    "deploy_environment:",
    "- dev",
    "- stg",
    "- prd",
    "environment: ${{ inputs.deploy_environment }}",
    "DEPLOY_ENVIRONMENT: ${{ inputs.deploy_environment }}",
)


def test_bootstrap_workflow_is_environment_scoped() -> None:
    workflow = BOOTSTRAP_WORKFLOW.read_text(encoding="utf-8")

    for snippet in REQUIRED_ENVIRONMENT_SNIPPETS:
        assert snippet in workflow

    assert "poma-gcp-wif-bootstrap-${{ inputs.deploy_environment }}" in workflow
    assert "poma/${DEPLOY_ENVIRONMENT}/gcp-wif-bootstrap" in workflow
    assert "poma-${DEPLOY_ENVIRONMENT}-github" in workflow
    assert "poma-${DEPLOY_ENVIRONMENT}-github-deployer" in workflow
    assert "--env \"${DEPLOY_ENVIRONMENT}\"" in workflow
    assert "poma-${DEPLOY_ENVIRONMENT}-free-tier" in workflow
    assert 'upsert_variable APP_ENV "${DEPLOY_ENVIRONMENT}"' in workflow


def test_deploy_workflow_is_environment_scoped() -> None:
    workflow = DEPLOY_WORKFLOW.read_text(encoding="utf-8")

    for snippet in REQUIRED_ENVIRONMENT_SNIPPETS:
        assert snippet in workflow

    assert "poma-gcp-free-tier-deploy-${{ inputs.deploy_environment }}" in workflow
    assert "poma/${DEPLOY_ENVIRONMENT}/gcp-free-tier" in workflow
    assert "APP_ENV=${APP_ENV} must match deploy_environment=${DEPLOY_ENVIRONMENT}" in workflow
    assert "ORDER_STATUS_TIMEOUT_SECONDS: ${{ vars.ORDER_STATUS_TIMEOUT_SECONDS }}" in workflow
    assert "CANCEL_STALE_ORDERS: ${{ vars.CANCEL_STALE_ORDERS }}" in workflow
