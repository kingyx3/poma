from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = REPO_ROOT / ".github/workflows/clear-rebalance-state.yml"


def test_clear_state_can_run_monitor_after_clear() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "run_monitor_after_clear" in workflow
    assert "RUN_MONITOR_AFTER_CLEAR" in workflow
    assert "docker-compose.vm.yml" in workflow
    assert "poma monitor" in workflow
    assert "sudo rm -f /opt/poma/state/rebalance_state.json" in workflow
