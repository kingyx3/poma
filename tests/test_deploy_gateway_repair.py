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


def test_reconcile_cron_never_fires_in_the_same_minute_as_monitor() -> None:
    """Reconcile must get timeout-policy opportunities without colliding with monitor.

    ``poma monitor`` fires every 5 minutes and all scheduled POMA commands share a non-blocking
    lock. Reconcile therefore runs at +2 and +4 minutes within each monitor interval: this keeps
    the jobs disjoint while ensuring a 120-second replacement threshold is observed before a
    300-second cancellation threshold under the normal schedule.
    """
    cron = POMA_CRON.read_text(encoding="utf-8")
    reconcile_line = next(
        line for line in cron.splitlines() if "poma reconcile-orders" in line and not line.startswith("#")
    )
    minute_field = reconcile_line.split(maxsplit=1)[0]

    minutes: set[int] = set()
    for stepped_range in minute_field.split(","):
        minute_range, step_text = stepped_range.split("/", 1)
        start_text, end_text = minute_range.split("-", 1)
        minutes.update(range(int(start_text), int(end_text) + 1, int(step_text)))

    monitor_minutes = set(range(0, 60, 5))
    expected_reconcile_minutes = {
        (monitor_minute + offset) % 60
        for monitor_minute in monitor_minutes
        for offset in (2, 4)
    }

    assert not minutes & monitor_minutes
    assert minutes == expected_reconcile_minutes


def test_deploy_does_not_provision_gateway_runtime() -> None:
    # IB Gateway runtime is owned by the IB Gateway Ops workflow, not the deploy step.
    # Auto CI/CD invokes Gateway Ops after relevant dev/stg deploys; manual deploys run it
    # explicitly. Guard against reintroducing the redundant/duplicate setup here.
    workflow = DEPLOY_WORKFLOW.read_text(encoding="utf-8")

    assert "install_ibc_config_helper.py" not in workflow
    assert "ensure_ibgateway_service.sh" not in workflow
    assert "repair_ib_gateway_runtime.py" not in workflow
