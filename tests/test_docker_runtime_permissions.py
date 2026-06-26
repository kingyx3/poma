from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = REPO_ROOT / "Dockerfile"
DEPLOY_SCRIPT = REPO_ROOT / "ops/scripts/deploy.sh"


def test_dockerfile_supports_host_uid_gid_build_args() -> None:
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")

    assert "ARG APP_UID=1000" in dockerfile
    assert "ARG APP_GID=1000" in dockerfile
    assert 'groupadd --gid "${APP_GID}" appuser' in dockerfile
    assert '--uid "${APP_UID}"' in dockerfile
    assert '--gid "${APP_GID}"' in dockerfile
    assert "USER appuser" in dockerfile


def test_deploy_script_builds_container_with_current_host_user() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    assert 'export POMA_UID="${POMA_UID:-$(id -u)}"' in script
    assert 'export POMA_GID="${POMA_GID:-$(id -g)}"' in script
    assert '--build-arg "APP_UID=${POMA_UID}"' in script
    assert '--build-arg "APP_GID=${POMA_GID}"' in script
    assert 'if [ ! -w "${dir}" ]; then' in script
