from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_bootstrap_docs_match_generated_deploy_config_contract() -> None:
    workflow = _text(REPO_ROOT / ".github/workflows/bootstrap-gcp-wif.yml")
    readme = _text(REPO_ROOT / "infra/gcp-wif-bootstrap/README.md")

    assert "Deploy reads this file directly; bootstrap no longer writes GitHub Variables." in workflow
    assert "ops/deploy/environments/<deploy_environment>.env" in readme
    assert "Bootstrap does not write GitHub Variables" in readme
    assert "GitHub Variables automatically" not in readme
    assert "copy the outputs into GitHub Variables" not in readme


def test_deploy_docs_include_current_manual_inputs_and_chat_discovery() -> None:
    workflow = _text(REPO_ROOT / ".github/workflows/deploy-gcp-vm.yml")
    deployment_docs = _text(REPO_ROOT / "docs/deployment-gcp-free-tier.md")

    assert "deploy_smoke:" in workflow
    assert "`deploy_smoke`" in deployment_docs
    assert "Discover Telegram chat ID" in deployment_docs
    assert "future chat-discovery" not in deployment_docs


def test_configuration_docs_match_app_env_default_and_deploy_override() -> None:
    config = _text(REPO_ROOT / "src/poma/config.py")
    env_example = _text(REPO_ROOT / ".env.example")
    configuration_docs = _text(REPO_ROOT / "docs/configuration.md")

    assert 'app_env: str = Field(default="development", alias="APP_ENV")' in config
    assert "APP_ENV=development" in env_example
    assert "| `APP_ENV` | yes | `development` |" in configuration_docs
    assert "CI/CD deploys render `dev`, `stg`, or `prd`" in configuration_docs


def test_workflow_runbook_does_not_require_yahoo_provider_key() -> None:
    workflow_runbook = _text(REPO_ROOT / "docs/workflow-runbook.md")
    configuration_docs = _text(REPO_ROOT / "docs/configuration.md")

    assert "data provider key" not in workflow_runbook
    assert "| `DATA_PROVIDER` | yes | `yahoo` |" in configuration_docs


def test_production_readiness_docs_include_execution_price_gates() -> None:
    readiness = _text(REPO_ROOT / "docs/production-readiness.md")

    for snippet in (
        "Strategy allocations contain no non-`cash` sleeve",
        "Paper/live mode uses anything other than `EXECUTION_PRICE_SOURCE=ibkr`",
        "Paper/live mode allows execution quotes older than 120 seconds",
        "Live mode allows delayed execution quotes",
    ):
        assert snippet in readiness


def test_image_deploy_docs_reject_mutable_main_fallback() -> None:
    deploy_docs = _text(REPO_ROOT / "docs/e2-micro-image-pull-deploy.md")
    workflow_docs = _text(REPO_ROOT / "docs/deployment-gcp-free-tier.md")

    assert "leaving `image` blank fails" in deploy_docs
    assert "Blank values and mutable tags are rejected" in workflow_docs
    assert "falls back to the `:main` tag" not in deploy_docs
