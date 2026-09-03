from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
GATEWAY_OPS_WORKFLOW = REPO_ROOT / ".github/workflows/ib-gateway-ops.yml"


def _workflow() -> str:
    return GATEWAY_OPS_WORKFLOW.read_text(encoding="utf-8")


def test_app_logs_split_reconcile_and_monitor_into_dedicated_steps() -> None:
    workflow = _workflow()

    reconcile_step = "- name: Show reconcile cron log"
    monitor_step = "- name: Show monitor cron log"

    assert reconcile_step in workflow
    assert monitor_step in workflow
    assert workflow.index(reconcile_step) < workflow.index(monitor_step)
    assert "poma-reconcile-cron.log" in workflow.split(reconcile_step, 1)[1].split(monitor_step, 1)[0]
    assert "poma-cron.log" in workflow.split(monitor_step, 1)[1].split("- name: Show rebalance state", 1)[0]


def test_app_logs_do_not_run_the_combined_gateway_helper() -> None:
    workflow = _workflow()
    operation_step = workflow.split("- name: Run Gateway operation over IAP SSH", 1)[1].split(
        "- name: Notify Telegram", 1
    )[0]

    assert "inputs.action != 'logs' && inputs.action != 'app-logs'" in operation_step


def test_app_logs_show_both_possible_crontab_owners() -> None:
    workflow = _workflow()
    scheduler_step = workflow.split("- name: Show app scheduler status", 1)[1].split(
        "- name: Show reconcile cron log", 1
    )[0]

    assert "sudo crontab -l -u ubuntu" in scheduler_step
    assert "sudo crontab -l -u poma" in scheduler_step
