from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_root_sitecustomize_guards_gateway_runner_only() -> None:
    hook = (REPO_ROOT / "sitecustomize.py").read_text(encoding="utf-8")

    assert 'Path(sys.argv[0]).name == "run_gateway_ops_workflow.py"' in hook
    assert "VISIBLE_STARTUP_CHECK_STATUS=failed" in hook
    assert "VISIBLE_IBKR_CHECK_STATUS=failed" in hook
    assert "preserving Gateway state" in hook
    assert "raise SystemExit(1)" in hook
