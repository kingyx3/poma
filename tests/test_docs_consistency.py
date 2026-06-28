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
