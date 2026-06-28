from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CI_WORKFLOW = REPO_ROOT / ".github/workflows/ci.yml"
BOOTSTRAP_WORKFLOW = REPO_ROOT / ".github/workflows/bootstrap-gcp-wif.yml"
DEPLOY_WORKFLOW = REPO_ROOT / ".github/workflows/deploy-gcp-vm.yml"
GATEWAY_OPS_WORKFLOW = REPO_ROOT / ".github/workflows/ib-gateway-ops.yml"
AUTO_CICD_WORKFLOW = REPO_ROOT / ".github/workflows/auto-cicd.yml"
GATEWAY_DIAGNOSTICS_HELPER = REPO_ROOT / "ops/scripts/diagnose_ib_gateway_runtime.py"

REQUIRED_ENVIRONMENT_SNIPPETS = (
    "deploy_environment:",
    "- dev",
    "- stg",
    "- prd",
    "environment: ${{ inputs.deploy_environment }}",
    "DEPLOY_ENVIRONMENT: ${{ inputs.deploy_environment }}",
)

OLD_ACTION_SNIPPETS = (
    "google-github-actions/auth@6fc4af4b145ae7821d527454aa9bd537d1f2dc5f",
    "google-github-actions/setup-gcloud@6189d56e4096ee891640bb02ac264be376592d6a",
    "hashicorp/setup-terraform@b9cd54a3c349d3f38e8881555d616ced269862dd",
    "actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683",
)


def test_ci_workflow_uses_current_action_versions() -> None:
    workflow = CI_WORKFLOW.read_text(encoding="utf-8")

    assert "actions/checkout@v5" in workflow
    assert "actions/setup-python@v6" in workflow
    assert "hashicorp/setup-terraform@v4" in workflow
    for snippet in OLD_ACTION_SNIPPETS:
        assert snippet not in workflow


def test_bootstrap_workflow_is_environment_scoped() -> None:
    workflow = BOOTSTRAP_WORKFLOW.read_text(encoding="utf-8")

    for snippet in REQUIRED_ENVIRONMENT_SNIPPETS:
        assert snippet in workflow

    assert "poma-gcp-wif-bootstrap-${{ inputs.deploy_environment }}" in workflow
    assert "poma/${DEPLOY_ENVIRONMENT}/gcp-wif-bootstrap" in workflow
    assert "WIF_POOL_ID: poma-${{ inputs.deploy_environment }}-github" in workflow
    assert (
        "WIF_SERVICE_ACCOUNT_ID: "
        "poma-${{ inputs.deploy_environment }}-github-deployer"
    ) in workflow
    assert '--pool-id "${WIF_POOL_ID}"' in workflow
    assert '-var="pool_id=${WIF_POOL_ID}"' in workflow
    assert 'config_path="${config_dir}/${DEPLOY_ENVIRONMENT}.env"' in workflow
    assert (
        "Deploy reads this file directly; bootstrap no longer writes "
        "GitHub Variables."
    ) in workflow


def test_bootstrap_workflow_uses_current_action_versions() -> None:
    workflow = BOOTSTRAP_WORKFLOW.read_text(encoding="utf-8")

    assert "actions/checkout@v5" in workflow
    assert "google-github-actions/setup-gcloud@v3" in workflow
    assert "hashicorp/setup-terraform@v4" in workflow
    assert "google-github-actions/auth@" not in workflow
    for snippet in OLD_ACTION_SNIPPETS:
        assert snippet not in workflow


def test_deploy_workflow_is_environment_scoped() -> None:
    workflow = DEPLOY_WORKFLOW.read_text(encoding="utf-8")

    for snippet in REQUIRED_ENVIRONMENT_SNIPPETS:
        assert snippet in workflow

    assert "poma-gcp-free-tier-deploy-${{ inputs.deploy_environment }}" in workflow
    assert "poma/${DEPLOY_ENVIRONMENT}/gcp-free-tier" in workflow
    assert 'set_env APP_ENV "${DEPLOY_ENVIRONMENT}"' in workflow
    assert "APP_ENV=${APP_ENV} must match deploy_environment=${DEPLOY_ENVIRONMENT}" in workflow
    assert 'set_default ORDER_STATUS_TIMEOUT_SECONDS "60"' in workflow
    assert 'set_default CANCEL_STALE_ORDERS "true"' in workflow


def test_deploy_workflow_routes_paper_to_paper_account() -> None:
    workflow = DEPLOY_WORKFLOW.read_text(encoding="utf-8")

    assert "IBKR_ACCOUNT_PAPER: ${{ secrets.IBKR_ACCOUNT_PAPER }}" in workflow
    assert 'case "${TRADING_MODE}" in' in workflow
    assert 'set_env IBKR_ACCOUNT "${IBKR_ACCOUNT_PAPER}"' in workflow
    assert "IBKR_ACCOUNT_PAPER GitHub Environment secret is required" in workflow
    assert "IBKR_ACCOUNT GitHub Environment secret is required when TRADING_MODE=live" in workflow


def test_deploy_workflow_uses_current_action_versions() -> None:
    workflow = DEPLOY_WORKFLOW.read_text(encoding="utf-8")

    assert "actions/checkout@v5" in workflow
    assert "google-github-actions/auth@v3" in workflow
    assert "google-github-actions/setup-gcloud@v3" in workflow
    assert "hashicorp/setup-terraform@v4" in workflow
    for snippet in OLD_ACTION_SNIPPETS:
        assert snippet not in workflow


def test_gateway_ops_workflow_is_environment_scoped() -> None:
    workflow = GATEWAY_OPS_WORKFLOW.read_text(encoding="utf-8")

    for snippet in REQUIRED_ENVIRONMENT_SNIPPETS:
        assert snippet in workflow

    assert "poma-ib-gateway-ops-${{ inputs.deploy_environment }}" in workflow
    assert "ops/deploy/environments/${DEPLOY_ENVIRONMENT}.env" in workflow
    assert "systemctl restart ibgateway" in workflow
    assert "journalctl -u ibgateway" in workflow
    assert "nc -z 127.0.0.1 7497" in workflow


def test_gateway_ops_workflow_can_configure_gateway_from_environment_secrets() -> None:
    workflow = GATEWAY_OPS_WORKFLOW.read_text(encoding="utf-8")

    assert "configure-paper" in workflow
    assert "configure-live" in workflow
    assert "IBKR_LOGIN_ID: ${{ secrets.IBKR_LOGIN_ID }}" in workflow
    assert "IBKR_LOGIN_SECRET: ${{ secrets.IBKR_LOGIN_SECRET }}" in workflow
    assert "sudo poma-configure-ibc" in workflow
    assert "printf '%s" in workflow


def test_gateway_ops_workflow_repairs_runtime_before_mutating_ops() -> None:
    workflow = GATEWAY_OPS_WORKFLOW.read_text(encoding="utf-8")

    assert "repair_gateway_runtime" in workflow
    assert "Repairing IB Gateway runtime helpers and installers on the VM." in workflow
    assert "repair_ib_gateway_runtime.py" in workflow
    assert "install_ibc_config_helper.py" in workflow
    assert "ensure_ibgateway_service.sh" in workflow
    assert "diagnose_ib_gateway_runtime.py" in workflow
    assert "sudo python3 /tmp/repair_ib_gateway_runtime.py" in workflow
    assert "sudo python3 /tmp/install_ibc_config_helper.py" in workflow
    assert "sudo sh /tmp/ensure_ibgateway_service.sh" in workflow


def test_gateway_ops_workflow_verifies_real_api_handshake() -> None:
    workflow = GATEWAY_OPS_WORKFLOW.read_text(encoding="utf-8")

    # A reachable socket is not enough; configure actions must confirm a real authenticated
    # ib_insync handshake through the deployed container after broker login completes.
    assert "verify_api_handshake" in workflow
    assert "poma ibkr-check" in workflow
    assert "nc -z 127.0.0.1 7497" in workflow


def test_gateway_ops_workflow_reports_runtime_logs_on_socket_failure() -> None:
    workflow = GATEWAY_OPS_WORKFLOW.read_text(encoding="utf-8")
    diagnostics = GATEWAY_DIAGNOSTICS_HELPER.read_text(encoding="utf-8")

    assert "diagnose_gateway_failure" in workflow
    assert "poma-diagnose-ibgateway diagnose" in workflow
    assert "systemctl status ibgateway" in diagnostics
    assert "journalctl" in diagnostics
    assert "/var/log/poma/ibgateway" in diagnostics
    assert "/tmp/poma-ibgateway" in diagnostics
    assert "/home/poma/ibc/logs" in diagnostics


def test_gateway_ops_workflow_uses_current_action_versions() -> None:
    workflow = GATEWAY_OPS_WORKFLOW.read_text(encoding="utf-8")

    assert "actions/checkout@v5" in workflow
    assert "google-github-actions/auth@v3" in workflow
    assert "google-github-actions/setup-gcloud@v3" in workflow
    for snippet in OLD_ACTION_SNIPPETS:
        assert snippet not in workflow


def test_deploy_and_ops_workflows_are_reusable() -> None:
    deploy = DEPLOY_WORKFLOW.read_text(encoding="utf-8")
    ops = GATEWAY_OPS_WORKFLOW.read_text(encoding="utf-8")

    # Both keep manual dispatch and gain workflow_call so the orchestrator can reuse them.
    assert "workflow_dispatch:" in deploy and "workflow_call:" in deploy
    assert "workflow_dispatch:" in ops and "workflow_call:" in ops


def test_workflows_send_env_tagged_telegram_notifications() -> None:
    action = (REPO_ROOT / ".github/actions/telegram-notify/action.yml").read_text(encoding="utf-8")
    assert "api.telegram.org" in action
    assert "using: composite" in action

    for workflow in (
        DEPLOY_WORKFLOW.read_text(encoding="utf-8"),
        GATEWAY_OPS_WORKFLOW.read_text(encoding="utf-8"),
    ):
        assert "uses: ./.github/actions/telegram-notify" in workflow
        assert "POMA[${{ inputs.deploy_environment }}]" in workflow
        assert "if: ${{ always() }}" in workflow
        assert "${{ secrets.TELEGRAM_BOT_TOKEN }}" in workflow


def test_auto_cicd_deploys_dev_on_pr_stg_on_merge_and_prd_on_release() -> None:
    workflow = AUTO_CICD_WORKFLOW.read_text(encoding="utf-8")

    # Triggers: every PR push (opened/reopened/synchronize) for dev, push to main for stg,
    # published release for prd.
    assert "pull_request:" in workflow
    assert "types: [opened, reopened, synchronize]" in workflow
    assert "push:" in workflow
    assert "branches: [main]" in workflow
    assert "release:" in workflow
    assert "types: [published]" in workflow

    # A newer PR push cancels the previous push's in-progress deploy.
    assert "cancel-in-progress: true" in workflow

    # Reuses the deploy + gateway-ops workflows.
    assert "uses: ./.github/workflows/deploy-gcp-vm.yml" in workflow
    assert "uses: ./.github/workflows/ib-gateway-ops.yml" in workflow
    assert "secrets: inherit" in workflow


def test_auto_cicd_gateway_actions_per_environment() -> None:
    workflow = AUTO_CICD_WORKFLOW.read_text(encoding="utf-8")

    dev_gateway = workflow.split("  dev-configure-gateway:", 1)[1].split("  stg-deploy:", 1)[0]
    stg_gateway = workflow.split("  stg-configure-gateway:", 1)[1].split("  prd-deploy:", 1)[0]
    prd_gateway = workflow.split("  prd-configure-gateway:", 1)[1]

    # PR checks must not require human IBKR mobile 2FA. They validate that the runtime
    # helpers install and the systemd service restarts cleanly. Staging and production keep
    # the credentialed broker configure paths where human approval is operationally expected.
    assert "action: restart" in dev_gateway
    assert "action: configure-paper" not in dev_gateway
    assert "action: configure-paper" in stg_gateway
    assert "action: configure-live" in prd_gateway


def test_adr_0002_dev_gateway_pr_checks_are_2fa_free_exists() -> None:
    adr = REPO_ROOT / "docs/adr/0002-dev-gateway-pr-checks-avoid-2fa.md"
    assert adr.exists(), "ADR 0002 must document that PR gateway checks avoid 2FA"
    text = adr.read_text(encoding="utf-8")
    assert "restart" in text
    assert "2FA" in text or "2fa" in text.lower()
