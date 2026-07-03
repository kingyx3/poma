import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
GATEWAY_OPS_RUNNER = REPO_ROOT / "ops/scripts/run_gateway_ops_workflow.py"


def _load_gateway_runner():
    spec = importlib.util.spec_from_file_location("run_gateway_ops_workflow", GATEWAY_OPS_RUNNER)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_gateway_paper_disclaimer_error_skips_fresh_login_restart() -> None:
    module = _load_gateway_runner()

    classification = module.classify_non_retriable_ibkr_check(
        "Error 10141, reqId -1: Paper trading disclaimer must first be accepted for API connection."
    )

    assert classification is not None
    reason, next_action = classification
    assert "paper account" in reason
    assert "manual disclaimer acceptance" in reason
    assert "accept the paper trading/API disclaimer" in next_action
    assert "rerun Gateway configure" in next_action
