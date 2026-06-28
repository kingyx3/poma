from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEPLOY_WORKFLOW = REPO_ROOT / ".github/workflows/deploy-gcp-vm.yml"
DEPLOY_SCRIPT = REPO_ROOT / "ops/scripts/deploy.sh"
DOCKERFILE = REPO_ROOT / "Dockerfile"


def test_upload_install_step_reports_stage_timings() -> None:
    workflow = DEPLOY_WORKFLOW.read_text(encoding="utf-8")

    expected_snippets = (
        "Packaged runtime archive:",
        "timed \"VM readiness\" ensure_vm_ready",
        "timed \"App package upload\"",
        "timed \"Environment upload\"",
        "timed \"Remote install, Docker build, smoke, cron\"",
        "REMOTE TIMING BEGIN",
        "remote_timed \"Docker build and smoke test\"",
        "remote_timed \"Install cron schedule\"",
        "TIMING Upload and install on VM total",
    )
    for snippet in expected_snippets:
        assert snippet in workflow


def test_deploy_workflow_bounds_expensive_steps() -> None:
    workflow = DEPLOY_WORKFLOW.read_text(encoding="utf-8")

    expected_snippets = (
        "timeout --kill-after=30s 2m python ops/scripts/render_env.py",
        "timeout --kill-after=30s 2m pip install -e .",
        "timeout --kill-after=30s 5m python ops/scripts/validate_data_provider.py",
        "timeout --kill-after=30s 2m gcloud config set project",
        "timeout --kill-after=30s 5m terraform -chdir=infra/gcp-free-tier init",
        "timeout --kill-after=30s 10m terraform -chdir=infra/gcp-free-tier plan",
        "timeout --kill-after=30s 20m terraform -chdir=infra/gcp-free-tier apply",
        "timeout --kill-after=30s 2m tar",
        "timeout-minutes: 35",
        "Remote install, Docker build, smoke, cron",
    )
    for snippet in expected_snippets:
        assert snippet in workflow


def test_deploy_polling_and_retries_are_bounded() -> None:
    workflow = DEPLOY_WORKFLOW.read_text(encoding="utf-8")

    assert "local deadline=$((SECONDS + 240)) attempt=0" in workflow
    assert "startup revision ${startup_revision} not ready within 4m" in workflow
    assert "retry_with_backoff" in workflow
    assert "max_attempts" in workflow
    assert "failed after ${attempt} attempt(s)" in workflow
    assert "timeout --kill-after=30s" in workflow
    assert "timed out; check VM readiness" in workflow


def test_vm_deploy_script_reports_build_smoke_and_cache_usage() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    expected_snippets = (
        "timed \"runtime directory checks\"",
        "timed \"docker build\" build_image",
        "Docker disk/cache usage before build",
        "Docker disk/cache usage after build",
        "DOCKER_BUILDKIT=1 COMPOSE_DOCKER_CLI_BUILD=1 docker compose build",
        "--progress=plain",
        "timed \"deploy smoke test\" run_deploy_smoke",
        "timed \"dangling image prune\" prune_dangling_images",
    )
    for snippet in expected_snippets:
        assert snippet in script


def test_dockerfile_uses_buildkit_pip_cache_mounts() -> None:
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")

    assert dockerfile.startswith("# syntax=docker/dockerfile:")
    assert "--mount=type=cache,target=/root/.cache/pip" in dockerfile
    assert "PIP_DISABLE_PIP_VERSION_CHECK=1" in dockerfile
    assert dockerfile.index("COPY pyproject.toml README.md ./") < dockerfile.index("COPY src ./src")
    assert dockerfile.index("ARG APP_UID=1000") > dockerfile.index("COPY src ./src")
