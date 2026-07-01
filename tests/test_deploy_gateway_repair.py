from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEPLOY_WORKFLOW = REPO_ROOT / ".github/workflows/deploy-gcp-vm.yml"
POMA_CRON = REPO_ROOT / "ops/cron/poma.cron"


def test_deploy_runs_app_install_and_cron() -> None:
    workflow = DEPLOY_WORKFLOW.read_text(encoding="utf-8")

    assert "bash ops/scripts/deploy.sh" in workflow
    assert "crontab ops/cron/poma.cron" in workflow


def test_deployed_cron_schedules_order_reconciliation() -> None:
    """Working orders must be followed up independent of the rebalance process lifetime.

    Without this cron entry, an accepted-but-unfilled order is never replaced or cancelled
    (see ``ExecutionManager.reconcile``), so it can sit open indefinitely and block the next
    session's rebalance via the stale-order check.
    """
    cron = POMA_CRON.read_text(encoding="utf-8")

    assert "poma reconcile-orders" in cron


def test_deploy_does_not_provision_gateway_runtime() -> None:
    # IB Gateway runtime is owned by the IB Gateway Ops workflow, not the deploy step.
    # Auto CI/CD invokes Gateway Ops after relevant dev/stg deploys; manual deploys run it
    # explicitly. Guard against reintroducing the redundant/duplicate setup here.
    workflow = DEPLOY_WORKFLOW.read_text(encoding="utf-8")

    assert "install_ibc_config_helper.py" not in workflow
    assert "ensure_ibgateway_service.sh" not in workflow
    assert "repair_ib_gateway_runtime.py" not in workflow
