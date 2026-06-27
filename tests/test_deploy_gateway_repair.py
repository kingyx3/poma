from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEPLOY_WORKFLOW = REPO_ROOT / ".github/workflows/deploy-gcp-vm.yml"


def test_deploy_runs_app_install_and_cron() -> None:
    workflow = DEPLOY_WORKFLOW.read_text(encoding="utf-8")

    assert "bash ops/scripts/deploy.sh" in workflow
    assert "crontab ops/cron/poma.cron" in workflow


def test_deploy_does_not_provision_gateway_runtime() -> None:
    # IB Gateway runtime is owned by the IB Gateway Ops workflow (run after every deploy), not
    # the deploy step. Guard against reintroducing the redundant/duplicate setup here.
    workflow = DEPLOY_WORKFLOW.read_text(encoding="utf-8")

    assert "install_ibc_config_helper.py" not in workflow
    assert "ensure_ibgateway_service.sh" not in workflow
    assert "repair_ib_gateway_runtime.py" not in workflow
