from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
RESOLVER = REPO_ROOT / "ops/scripts/resolve_gcp_deploy_env.sh"
DEPLOY_WORKFLOW = REPO_ROOT / ".github/workflows/deploy-gcp-vm.yml"
GATEWAY_OPS_WORKFLOW = REPO_ROOT / ".github/workflows/ib-gateway-ops.yml"
ENV_CONFIG_PATH = "ops/deploy/environments/${DEPLOY_ENVIRONMENT}.env"


def test_gcp_env_resolver_exports_shared_deploy_settings() -> None:
    script = RESOLVER.read_text(encoding="utf-8")

    assert "GCP_WORKLOAD_IDENTITY_PROVIDER" in script
    assert "GCP_SERVICE_ACCOUNT_EMAIL" in script
    assert "set_env GCP_PROJECT_ID" in script
    assert "set_env GCP_PROJECT_NUMBER" in script
    assert "set_default GCP_REGION" in script
    assert "set_default GCP_ZONE" in script
    assert "set_default GCP_VM_NAME" in script
    assert "set_default TF_STATE_BUCKET" in script


def test_deploy_workflow_uses_shared_gcp_env_resolver() -> None:
    workflow = DEPLOY_WORKFLOW.read_text(encoding="utf-8")

    assert "source ops/scripts/resolve_gcp_deploy_env.sh" in workflow
    assert f'config_path="{ENV_CONFIG_PATH}"' not in workflow


def test_gateway_ops_can_adopt_shared_gcp_env_resolver() -> None:
    workflow = GATEWAY_OPS_WORKFLOW.read_text(encoding="utf-8")
    script = RESOLVER.read_text(encoding="utf-8")

    assert ENV_CONFIG_PATH in workflow
    assert ENV_CONFIG_PATH in script
