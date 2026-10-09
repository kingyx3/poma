from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_gateway_configure_actions_restart_then_gate_by_mode() -> None:
    workflow = (REPO_ROOT / ".github/workflows/ib-gateway-ops.yml").read_text(encoding="utf-8")
    runner = (REPO_ROOT / "ops/scripts/run_gateway_ops_workflow.py").read_text(encoding="utf-8")

    assert "python3 ops/scripts/run_gateway_ops_workflow.py" in workflow
    assert "configure-paper" in workflow
    assert "configure-live" in workflow
    assert "Checkpoint Gateway auth logs" in runner
    assert "Force fresh ibgateway login after IBC configuration" in runner
    assert "Fresh 2FA challenge wait is enforced for live configure only" in runner
    assert "Fresh live 2FA challenge wait" in runner
    assert "api_ready(mode, required=True)" in runner
    assert "poma ibkr-check" in runner

    restart = runner.index("Restart ibgateway after IBC configuration")
    paper_branch = runner.index('if mode == "paper"')
    paper_ready = runner.index("return api_ready(mode, required=True)", paper_branch)
    live_wait = runner.index("Fresh live 2FA challenge wait")
    live_ready = runner.index("readiness = api_ready(mode, required=True)", live_wait)

    assert restart < paper_branch < paper_ready < live_wait < live_ready


def test_gateway_verify_socket_is_the_strict_readiness_gate() -> None:
    workflow = (REPO_ROOT / ".github/workflows/ib-gateway-ops.yml").read_text(encoding="utf-8")
    runner = (REPO_ROOT / "ops/scripts/run_gateway_ops_workflow.py").read_text(encoding="utf-8")

    assert "python3 ops/scripts/run_gateway_ops_workflow.py" in workflow
    assert "verify-socket" in workflow
    assert 'if action == "verify-socket"' in runner
    assert 'return api_ready("live" if deploy_environment == "prd" else "paper", required=True)' in runner
    assert "poma ibkr-check" in runner


def test_live_schedule_is_installed_only_after_authenticated_readiness():
    workflow = (REPO_ROOT / ".github/workflows/deploy-gcp-vm.yml").read_text()
    runner = (REPO_ROOT / "ops/scripts/run_gateway_ops_workflow.py").read_text()
    block = workflow.split('if [ "${{ inputs.trading_mode }}" = "live" ]; then', 1)[1].split('            fi', 1)[0]
    assert 'crontab' not in block.split('else', 1)[0]
    assert 'crontab ops/cron/poma.cron' in block.split('else', 1)[1]
    enable = runner.index('Enable authenticated live schedule')
    readiness = runner.rindex('readiness = api_ready(mode, required=True)')
    assert readiness < runner.index('if readiness != 0:', readiness) < enable
    assert 'ALLOW_LIVE_TRADING=true' in runner[enable:]
