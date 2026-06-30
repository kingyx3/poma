from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CI_WORKFLOW = REPO_ROOT / ".github/workflows/ci.yml"
BOOTSTRAP_WORKFLOW = REPO_ROOT / ".github/workflows/bootstrap-gcp-wif.yml"
DEPLOY_WORKFLOW = REPO_ROOT / ".github/workflows/deploy-gcp-vm.yml"
GATEWAY_OPS_WORKFLOW = REPO_ROOT / ".github/workflows/ib-gateway-ops.yml"
CLEAR_REBALANCE_STATE_WORKFLOW = REPO_ROOT / ".github/workflows/clear-rebalance-state.yml"
AUTO_CICD_WORKFLOW = REPO_ROOT / ".github/workflows/auto-cicd.yml"
GATEWAY_DIAGNOSTICS_HELPER = REPO_ROOT / "ops/scripts/diagnose_ib_gateway_runtime.py"
GATEWAY_OPS_RUNNER = REPO_ROOT / "ops/scripts/run_gateway_ops_workflow.py"
GATEWAY_WAIT_HELPER = REPO_ROOT / "ops/scripts/wait_ib_gateway_2fa.py"

OLD_ACTION_SNIPPETS = (
    "google-github-actions/auth@6fc4af4b145ae7821d527454aa9bd537d1f2dc5f",
    "google-github-actions/setup-gcloud@6189d56e4096ee891640bb02ac264be376592d6a",
    "hashicorp/setup-terraform@b9cd54a3c349d3f38e8881555d616ced269862dd",
    "actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683",
)


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_workflows_use_current_action_versions() -> None:
    workflows = (
        _text(CI_WORKFLOW),
        _text(BOOTSTRAP_WORKFLOW),
        _text(DEPLOY_WORKFLOW),
        _text(GATEWAY_OPS_WORKFLOW),
        _text(CLEAR_REBALANCE_STATE_WORKFLOW),
    )
    combined = "\n".join(workflows)

    assert "actions/checkout@v5" in combined
    assert "hashicorp/setup-terraform@v4" in combined
    assert "google-github-actions/setup-gcloud@v3" in combined
    for snippet in OLD_ACTION_SNIPPETS:
        assert snippet not in combined


def test_deploy_workflow_routes_paper_to_paper_account() -> None:
    workflow = _text(DEPLOY_WORKFLOW)

    assert "IBKR_ACCOUNT_PAPER" in workflow
    assert 'case "${TRADING_MODE}" in' in workflow
    assert 'set_env IBKR_ACCOUNT "${IBKR_ACCOUNT_PAPER}"' in workflow
    assert "TRADING_MODE=live" in workflow


def test_gateway_ops_routes_paper_login_secrets_to_configure_paper() -> None:
    workflow = _text(GATEWAY_OPS_WORKFLOW)
    secret_resolver = workflow.split("Resolve broker login secrets", 1)[1].split("Resolve generated deployment config", 1)[0]
    paper_block = secret_resolver.split("configure-paper)", 1)[1].split(";;", 1)[0]
    live_block = secret_resolver.split("configure-live)", 1)[1].split(";;", 1)[0]

    assert "${{ secrets.IBKR_LOGIN_ID_PAPER }}" in workflow
    assert "${{ secrets.IBKR_LOGIN_SECRET_PAPER }}" in workflow
    assert "IBKR_LOGIN_ID_PAPER and IBKR_LOGIN_SECRET_PAPER" in paper_block
    assert 'echo "BROKER_LOGIN_ID=${IBKR_LOGIN_ID_PAPER}"' in paper_block
    assert 'echo "BROKER_LOGIN_VALUE=${IBKR_LOGIN_SECRET_PAPER}"' in paper_block
    assert 'echo "BROKER_LOGIN_ID=${IBKR_LOGIN_ID}"' in live_block
    assert 'echo "BROKER_LOGIN_VALUE=${IBKR_LOGIN_SECRET}"' in live_block


def test_gateway_ops_rejects_stg_configure_actions() -> None:
    workflow = _text(GATEWAY_OPS_WORKFLOW)
    scope_guard = workflow.split("Validate Gateway operation scope", 1)[1].split("Resolve broker login secrets", 1)[0]

    assert '"${DEPLOY_ENVIRONMENT}" = "stg"' in scope_guard
    assert "configure-paper|configure-live" in scope_guard
    assert "Gateway configure actions are intentionally disabled for stg" in scope_guard


def test_gateway_ops_workflow_core_contract() -> None:
    workflow = _text(GATEWAY_OPS_WORKFLOW)
    runner = _text(GATEWAY_OPS_RUNNER)
    wait_helper = _text(GATEWAY_WAIT_HELPER)
    diagnostics = _text(GATEWAY_DIAGNOSTICS_HELPER)

    for snippet in (
        "workflow_dispatch:",
        "workflow_call:",
        "poma-ib-gateway-ops-${{ inputs.deploy_environment }}",
        "configure-paper",
        "configure-live",
        "python3 ops/scripts/run_gateway_ops_workflow.py",
    ):
        assert snippet in workflow

    for snippet in (
        "poma-configure-ibc",
        "repair_runtime",
        "poma ibkr-check",
        "Gateway API socket stability guard",
        "poma-wait-ibgateway-2fa",
    ):
        assert snippet in runner

    assert "Fresh 2FA startup classification" in wait_helper
    assert "systemctl status ibgateway" in diagnostics
    assert "journalctl" in diagnostics
    assert "/var/log/poma/ibgateway" in diagnostics
    assert "/home/poma/ibc/logs" in diagnostics


def test_clear_rebalance_state_workflow_is_manual_dev_stg_only() -> None:
    workflow = _text(CLEAR_REBALANCE_STATE_WORKFLOW)

    for snippet in (
        "name: Clear Rebalance Run State",
        "workflow_dispatch:",
        "options: [dev, stg]",
        "source ops/scripts/resolve_gcp_deploy_env.sh",
        "google-github-actions/auth@v3",
        "google-github-actions/setup-gcloud@v3",
        "sudo rm -f /opt/poma/state/rebalance_state.json",
        "POMA[${{ inputs.deploy_environment }}] clear-rebalance-state",
    ):
        assert snippet in workflow
    assert "workflow_call:" not in workflow


def test_deploy_and_ops_workflows_send_environment_tagged_alerts() -> None:
    for workflow in (_text(DEPLOY_WORKFLOW), _text(GATEWAY_OPS_WORKFLOW)):
        assert "uses: ./.github/actions/telegram-notify" in workflow
        assert "POMA[${{ inputs.deploy_environment }}]" in workflow
        assert "if: ${{ always() }}" in workflow


def test_auto_cicd_deploys_dev_stg_and_prd() -> None:
    workflow = _text(AUTO_CICD_WORKFLOW)

    for snippet in (
        "pull_request:",
        "types: [opened, reopened, synchronize]",
        "push:",
        "branches: [main]",
        "release:",
        "types: [published]",
        "uses: ./.github/workflows/deploy-gcp-vm.yml",
        "uses: ./.github/workflows/ib-gateway-ops.yml",
        "cancel-in-progress: true",
    ):
        assert snippet in workflow


def test_auto_cicd_change_detection_scopes_deploy_gateway_and_shared_paths() -> None:
    workflow = _text(AUTO_CICD_WORKFLOW)
    dev_gateway = workflow.split("  dev-configure-gateway:", 1)[1].split("  stg-deploy:", 1)[0]
    shared_paths = workflow.split("is_shared_vm_gateway_path()", 1)[1].split("is_deploy_path()", 1)[0]
    deploy_paths = workflow.split("is_deploy_path()", 1)[1].split("is_gateway_path()", 1)[0]
    gateway_paths = workflow.split("is_gateway_path()", 1)[1].split("case \"${EVENT_NAME}\"", 1)[0]

    assert "needs.changes.outputs.gateway_required == 'true'" in dev_gateway
    assert "needs.changes.outputs.deploy_required == 'true'" not in dev_gateway
    assert "Deploy and Gateway Ops are coupled only for shared VM foundation paths" in workflow
    assert "Any deploy-required change may apply" not in workflow

    assert ".github/workflows/auto-cicd.yml" in shared_paths
    assert "infra/gcp-free-tier" in shared_paths
    assert "ops/deploy/environments" in shared_paths

    assert ".github/workflows/deploy-gcp-vm.yml" in deploy_paths
    assert "pyproject.toml" in deploy_paths
    assert "ops/scripts/validate_runtime_config.py" in deploy_paths
    assert "src/*" in deploy_paths
    assert "ops/scripts/repair_ib_gateway_runtime.py" not in deploy_paths

    assert ".github/workflows/ib-gateway-ops.yml" in gateway_paths
    assert "ops/scripts/repair_ib_gateway_runtime.py" in gateway_paths
    assert "ops/scripts/wait_ib_gateway_2fa.py" in gateway_paths
    assert "ops/scripts/run_gateway_ops_workflow.py" in gateway_paths
    assert "src/*" not in gateway_paths
    assert "pyproject.toml" not in gateway_paths
    assert "infra/gcp-free-tier" not in gateway_paths
    assert "ops/deploy/environments" not in gateway_paths
    assert ".github/workflows/deploy-gcp-vm.yml" not in gateway_paths


def test_auto_cicd_gateway_actions_per_environment() -> None:
    workflow = _text(AUTO_CICD_WORKFLOW)
    dev_gateway = workflow.split("  dev-configure-gateway:", 1)[1].split("  stg-deploy:", 1)[0]
    stg_deploy = workflow.split("  stg-deploy:", 1)[1].split("  prd-deploy:", 1)[0]
    prd_gateway = workflow.split("  prd-configure-gateway:", 1)[1]

    assert "action: configure-paper" in dev_gateway
    assert "action: restart" not in dev_gateway
    assert "stg-configure-gateway" not in workflow
    assert "uses: ./.github/workflows/ib-gateway-ops.yml" not in stg_deploy
    assert "action: configure-paper" not in stg_deploy
    assert "action: configure-live" not in stg_deploy
    assert "action: configure-live" in prd_gateway


def test_adr_0002_dev_gateway_pr_checks_records_configure_paper_decision() -> None:
    adr = REPO_ROOT / "docs/adr/0002-dev-gateway-configure-paper-validation.md"
    text = adr.read_text(encoding="utf-8")
    assert "Status: Accepted" in text
    assert "action: configure-paper" in text
    assert "restart-only" in text
    assert "2FA" in text or "2fa" in text.lower()
