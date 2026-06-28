from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CI_WORKFLOW = REPO_ROOT / ".github/workflows/ci.yml"
BOOTSTRAP_WORKFLOW = REPO_ROOT / ".github/workflows/bootstrap-gcp-wif.yml"
DEPLOY_WORKFLOW = REPO_ROOT / ".github/workflows/deploy-gcp-vm.yml"
PAPER_DEPLOY_WORKFLOW = REPO_ROOT / ".github/workflows/deploy-paper-gcp-vm.yml"
GATEWAY_OPS_WORKFLOW = REPO_ROOT / ".github/workflows/ib-gateway-ops.yml"
AUTO_CICD_WORKFLOW = REPO_ROOT / ".github/workflows/auto-cicd.yml"
GATEWAY_DIAGNOSTICS_HELPER = REPO_ROOT / "ops/scripts/diagnose_ib_gateway_runtime.py"

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
        _text(PAPER_DEPLOY_WORKFLOW),
        _text(GATEWAY_OPS_WORKFLOW),
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


def test_gateway_ops_workflow_core_contract() -> None:
    workflow = _text(GATEWAY_OPS_WORKFLOW)
    diagnostics = _text(GATEWAY_DIAGNOSTICS_HELPER)

    for snippet in (
        "workflow_dispatch:",
        "workflow_call:",
        "poma-ib-gateway-ops-${{ inputs.deploy_environment }}",
        "configure-paper",
        "poma-configure-ibc",
        "repair_gateway_runtime",
        "verify_api_handshake",
        "poma ibkr-check",
        "required_stable=2",
        "Gateway API socket stability guard",
        "auth_pending_stage",
    ):
        assert snippet in workflow

    assert "systemctl status ibgateway" in diagnostics
    assert "journalctl" in diagnostics
    assert "/var/log/poma/ibgateway" in diagnostics
    assert "/home/poma/ibc/logs" in diagnostics


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
        "uses: ./.github/workflows/deploy-paper-gcp-vm.yml",
        "uses: ./.github/workflows/deploy-gcp-vm.yml",
        "cancel-in-progress: true",
        "deploy_environment: dev",
        "deploy_environment: stg",
        "deploy_environment: prd",
    ):
        assert snippet in workflow


def test_auto_cicd_routes_dev_stg_to_paper_deployment() -> None:
    workflow = _text(AUTO_CICD_WORKFLOW)
    paper_workflow = _text(PAPER_DEPLOY_WORKFLOW)

    assert "  dev-paper:" in workflow
    assert "  stg-paper:" in workflow
    assert "  prd-deploy:" in workflow
    assert "  prd-configure-gateway:" not in workflow
    assert "allow_live_trading: true" in workflow

    assert "trading_mode: paper" in paper_workflow
    assert "allow_live_trading: false" in paper_workflow
    assert "action: configure-paper" in paper_workflow


def test_auto_cicd_runs_gateway_ops_only_for_gateway_relevant_changes() -> None:
    workflow = _text(AUTO_CICD_WORKFLOW)
    paper_workflow = _text(PAPER_DEPLOY_WORKFLOW)
    gateway_paths = workflow.split("is_gateway_path()", 1)[1].split(
        "case \"${EVENT_NAME}\"", 1
    )[0]

    assert "gateway_required:" in workflow
    assert "needs.changes.outputs.gateway_required == 'true'" in workflow
    assert "deploy_required:" in workflow
    assert "needs.changes.outputs.deploy_required == 'true'" in workflow
    assert "inputs.gateway_required" in paper_workflow
    assert "ops/scripts/validate_runtime_config.py" in workflow
    assert ".github/workflows/deploy-paper-gcp-vm.yml" in gateway_paths
    assert ".github/workflows/ib-gateway-ops.yml" in gateway_paths
    assert "ops/scripts/repair_ib_gateway_runtime.py" in gateway_paths
    assert ".github/workflows/deploy-gcp-vm.yml" not in gateway_paths
    assert ".github/workflows/auto-cicd.yml" not in gateway_paths


def test_adr_0002_dev_gateway_pr_checks_records_configure_paper_decision() -> None:
    adr = REPO_ROOT / "docs/adr/0002-dev-gateway-configure-paper-validation.md"
    text = adr.read_text(encoding="utf-8")
    assert "Status: Accepted" in text
    assert "action: configure-paper" in text
    assert "restart-only" in text
    assert "2FA" in text or "2fa" in text.lower()
