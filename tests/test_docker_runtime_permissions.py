from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = REPO_ROOT / "Dockerfile"
IMAGE_WORKFLOW = REPO_ROOT / ".github/workflows/build-app-image.yml"
DEPLOY_SCRIPT = REPO_ROOT / "ops/scripts/deploy.sh"


def test_dockerfile_supports_host_uid_gid_build_args() -> None:
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")

    assert "ARG APP_UID=1000" in dockerfile
    assert "ARG APP_GID=1000" in dockerfile
    assert 'groupadd --gid "${APP_GID}" appuser' in dockerfile
    assert '--uid "${APP_UID}"' in dockerfile
    assert '--gid "${APP_GID}"' in dockerfile
    assert "USER appuser" in dockerfile


def test_image_workflow_builds_container_with_vm_runtime_identity() -> None:
    workflow = IMAGE_WORKFLOW.read_text(encoding="utf-8")

    assert '--build-arg "APP_UID=1000"' in workflow
    assert '--build-arg "APP_GID=1000"' in workflow


def test_deploy_script_checks_current_host_user_before_pull() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    assert 'export POMA_UID="${POMA_UID:-$(id -u)}"' in script
    assert 'export POMA_GID="${POMA_GID:-$(id -g)}"' in script
    assert 'EXPECTED_APP_UID="${EXPECTED_APP_UID:-1000}"' in script
    assert 'EXPECTED_APP_GID="${EXPECTED_APP_GID:-1000}"' in script
    assert "must match image app identity" in script
    assert "docker compose version >/dev/null" in script
    assert "docker image prune -f >/dev/null" in script
    assert 'if [ ! -w "${dir}" ]; then' in script
    assert "docker compose build" not in script


def test_dockerignore_keeps_runtime_build_context_minimal() -> None:
    dockerignore = (REPO_ROOT / ".dockerignore").read_text(encoding="utf-8")

    minimal_context_exclusions = (
        ".env",
        ".env.*",
        ".git",
        ".github",
        "docs",
        "tests",
        "infra",
        "ops",
        "reports",
        "state",
        "data",
        "logs",
    )
    for entry in minimal_context_exclusions:
        assert entry in dockerignore
