from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CI_WORKFLOW = REPO_ROOT / ".github/workflows/ci.yml"
BOOTSTRAP_WORKFLOW = REPO_ROOT / ".github/workflows/bootstrap-gcp-wif.yml"
DEPLOY_WORKFLOW = REPO_ROOT / ".github/workflows/deploy-gcp-vm.yml"
GATEWAY_OPS_WORKFLOW = REPO_ROOT / ".github/workflows/ib-gateway-ops.yml"


REQUIRED_ENVIRONMENT_SNIPPETS = (
    "deploy_environment:",
    "- dev",
    "- stg",
    "- prd",
    "environment: ${{ inputs.deploy_environment }}",
    "DEPLOY_ENVIRONMENT: ${{ inputs.deploy_environment }}",
)

OLD_ACTION_SNIPPETS = (
    "google-github-actions/auth@6fc4af4b145ae7821d527454aa9bd537d1f2dc5f",
    "google-github-actions/setup-gcloud@6189d56e4096ee891640bb02ac264be376592d6a",
    "hashicorp/setup-terraform@b9cd54a3c349d3f38e8881555d616ced269862dd",
    "actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683",
)


def test_ci_workflow_uses_current_action_versions() -> None:
    workflow = CI_WORKFLOW.read_text(encoding="utf-8")

    assert "actions/checkout@v5" in workflow
    assert "actions/setup-python@v6" in workflow
    assert "hashicorp/setup-terraform@v4" in workflow
    for snippet in OLD_ACTION_SNIPPETS:
        assert snippet not in workflow


def test_bootstrap_workflow_is_environment_scoped() -> None:
    workflow = BOOTSTRAP_WORKFLOW.read_text(encoding="utf-8")

    for snippet in REQUIRED_ENVIRONMENT_SNIPPETS:
        assert snippet in workflow

    assert "poma-gcp-wif-bootstrap-${{ inputs.deploy_environment }}" in workflow
    assert "poma/${DEPLOY_ENVIRONMENT}/gcp-wif-bootstrap" in workflow
    assert "WIF_POOL_ID: poma-${{ inputs.deploy_environment }}-github" in workflow
    assert (
        "WIF_SERVICE_ACCOUNT_ID: "
        "poma-${{ inputs.deploy_environment }}-github-deployer"
    ) in workflow
    assert '--pool-id "${WIF_POOL_ID}"' in workflow
    assert '-var="pool_id=${WIF_POOL_ID}"' in workflow
    assert 'config_path="${config_dir}/${DEPLOY_ENVIRONMENT}.env"' in workflow
    assert (
        "Deploy reads this file directly; bootstrap no longer writes "
        "GitHub Variables."
    ) in workflow


def test_bootstrap_workflow_uses_current_action_versions() -> None:
    workflow = BOOTSTRAP_WORKFLOW.read_text(encoding="utf-8")

    assert "actions/checkout@v5" in workflow
    assert "google-github-actions/setup-gcloud@v3" in workflow
    assert "hashicorp/setup-terraform@v4" in workflow
    assert "google-github-actions/auth@" not in workflow
    for snippet in OLD_ACTION_SNIPPETS:
        assert snippet not in workflow


def test_deploy_workflow_is_environment_scoped() -> None:
    workflow = DEPLOY_WORKFLOW.read_text(encoding="utf-8")

    for snippet in REQUIRED_ENVIRONMENT_SNIPPETS:
        assert snippet in workflow

    assert "poma-gcp-free-tier-deploy-${{ inputs.deploy_environment }}" in workflow
    assert "poma/${DEPLOY_ENVIRONMENT}/gcp-free-tier" in workflow
    assert 'set_env APP_ENV "${DEPLOY_ENVIRONMENT}"' in workflow
    assert "APP_ENV=${APP_ENV} must match deploy_environment=${DEPLOY_ENVIRONMENT}" in workflow
    assert 'set_default ORDER_STATUS_TIMEOUT_SECONDS "60"' in workflow
    assert 'set_default CANCEL_STALE_ORDERS "true"' in workflow


def test_deploy_workflow_uses_current_action_versions() -> None:
    workflow = DEPLOY_WORKFLOW.read_text(encoding="utf-8")

    assert "actions/checkout@v5" in workflow
    assert "google-github-actions/auth@v3" in workflow
    assert "google-github-actions/setup-gcloud@v3" in workflow
    assert "hashicorp/setup-terraform@v4" in workflow
    for snippet in OLD_ACTION_SNIPPETS:
        assert snippet not in workflow


def test_gateway_ops_workflow_is_environment_scoped() -> None:
    workflow = GATEWAY_OPS_WORKFLOW.read_text(encoding="utf-8")

    for snippet in REQUIRED_ENVIRONMENT_SNIPPETS:
        assert snippet in workflow

    assert "poma-ib-gateway-ops-${{ inputs.deploy_environment }}" in workflow
    assert "ops/deploy/environments/${DEPLOY_ENVIRONMENT}.env" in workflow
    assert "systemctl restart ibgateway" in workflow
    assert "journalctl -u ibgateway" in workflow
    assert "nc -z 127.0.0.1 7497" in workflow


def test_gateway_ops_workflow_can_configure_gateway_from_environment_secrets() -> None:
    workflow = GATEWAY_OPS_WORKFLOW.read_text(encoding="utf-8")

    assert "configure-paper" in workflow
    assert "configure-live" in workflow
    assert "IBKR_LOGIN_ID: ${{ secrets.IBKR_LOGIN_ID }}" in workflow
    assert "IBKR_LOGIN_SECRET: ${{ secrets.IBKR_LOGIN_SECRET }}" in workflow
    assert "sudo poma-configure-ibc" in workflow
    assert "printf '%s\\n%s\\n%s\\n'" in workflow


def test_gateway_ops_workflow_uses_current_action_versions() -> None:
    workflow = GATEWAY_OPS_WORKFLOW.read_text(encoding="utf-8")

    assert "actions/checkout@v5" in workflow
    assert "google-github-actions/auth@v3" in workflow
    assert "google-github-actions/setup-gcloud@v3" in workflow
    for snippet in OLD_ACTION_SNIPPETS:
        assert snippet not in workflow
