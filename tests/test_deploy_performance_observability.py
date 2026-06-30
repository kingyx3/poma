from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEPLOY_WORKFLOW = REPO_ROOT / ".github/workflows/deploy-gcp-vm.yml"
IMAGE_WORKFLOW = REPO_ROOT / ".github/workflows/build-app-image.yml"
DEPLOY_SCRIPT = REPO_ROOT / "ops/scripts/deploy.sh"
DOCKERFILE = REPO_ROOT / "Dockerfile"
COMPOSE_VM = REPO_ROOT / "docker-compose.vm.yml"
CONSTRAINTS = REPO_ROOT / "constraints.txt"


def test_upload_install_step_reports_stage_timings() -> None:
    workflow = DEPLOY_WORKFLOW.read_text(encoding="utf-8")

    expected_snippets = (
        "Packaged runtime archive:",
        "timed \"VM readiness\" ensure_vm_ready",
        "timed \"App package upload\"",
        "timed \"Environment upload\"",
        "timed \"Remote install, Docker pull, smoke, cron\"",
        "REMOTE TIMING BEGIN",
        "remote_timed \"Docker pull and smoke test\"",
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
        "docker-compose.vm.yml",
        "timeout-minutes: 55",
        "timeout-minutes: 90",
        "Remote install, Docker pull, smoke, cron",
    )
    for snippet in expected_snippets:
        assert snippet in workflow


def test_prebuilt_image_workflow_pushes_main_and_sha_tags_with_cache() -> None:
    workflow = IMAGE_WORKFLOW.read_text(encoding="utf-8")

    expected_snippets = (
        "branches:",
        "- main",
        "permissions:",
        "packages: write",
        "docker/setup-buildx-action@v3",
        "docker/login-action@v3",
        "docker buildx build",
        '--build-arg "APP_UID=1000"',
        '--build-arg "APP_GID=1000"',
        "--push",
        "--tag \"${image}:main\"",
        "--tag \"${image}:${GITHUB_SHA}\"",
        "--cache-from type=gha",
        "--cache-to type=gha,mode=max",
    )
    for snippet in expected_snippets:
        assert snippet in workflow


def test_deploy_polling_and_retries_are_bounded() -> None:
    workflow = DEPLOY_WORKFLOW.read_text(encoding="utf-8")

    assert "local deadline=$((SECONDS + 600)) attempt=0" in workflow
    assert "startup revision ${startup_revision} not ready within 10m" in workflow
    assert "id poma >/dev/null 2>&1" in workflow
    assert "systemctl is-active --quiet docker" in workflow
    assert "systemctl is-active --quiet cron" in workflow
    assert "test -f /var/lib/cloud/instance/boot-finished" not in workflow
    assert "print_vm_bootstrap_status" in workflow
    assert "retry_with_backoff" in workflow
    assert "max_attempts" in workflow
    assert "failed after ${attempt} attempt(s)" in workflow
    assert "timeout --kill-after=30s" in workflow
    assert "timed out; check VM readiness" in workflow


def test_vm_deploy_script_pulls_prebuilt_image_and_bounds_smoke() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    expected_snippets = (
        "timed \"runtime directory checks\"",
        "timed \"runtime identity checks\" ensure_runtime_identity",
        "timed \"compose image configuration\" write_compose_env",
        "timed \"vm compose file\" ensure_vm_compose_file",
        "timed \"docker image pull\" pull_image",
        "Docker disk/cache usage before image pull",
        "timeout_compose 8m pull poma",
        "timeout_compose 3m run --rm",
        "timed \"deploy smoke test\" run_deploy_smoke",
        "timed \"dangling image prune\" prune_dangling_images",
        "DEFAULT_IMAGE_TAG=\"${DEFAULT_IMAGE_TAG:-main}\"",
    )
    for snippet in expected_snippets:
        assert snippet in script

    assert "docker compose build" not in script
    assert 'timeout --kill-after=30s "${duration}" docker compose' in script
    assert "DOCKER_BUILDKIT" not in script


def test_vm_compose_uses_prebuilt_image() -> None:
    compose = COMPOSE_VM.read_text(encoding="utf-8")

    assert "image: ${POMA_IMAGE}" in compose
    assert "build:" not in compose
    assert "network_mode: host" in compose


def test_dockerfile_uses_buildkit_pip_cache_mounts_and_constraints() -> None:
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    constraints = CONSTRAINTS.read_text(encoding="utf-8")

    assert dockerfile.startswith("# syntax=docker/dockerfile:")
    assert "--mount=type=cache,target=/root/.cache/pip" in dockerfile
    assert "PIP_DISABLE_PIP_VERSION_CHECK=1" in dockerfile
    assert "COPY pyproject.toml README.md constraints.txt ./" in dockerfile
    assert "pip install --prefer-binary -c constraints.txt ." in dockerfile
    assert dockerfile.index("COPY pyproject.toml README.md constraints.txt ./") < dockerfile.index("COPY src ./src")
    assert dockerfile.index("ARG APP_UID=1000") > dockerfile.index("COPY src ./src")
    assert "pandas==2.2.2" in constraints
    assert "yfinance==0.2.64" in constraints
