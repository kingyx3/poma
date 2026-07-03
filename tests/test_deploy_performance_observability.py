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
        "timed \"Pause app cron before deploy\"",
        "timed \"Deployment bundle upload\"",
        "retry_with_backoff 4m \"Deployment bundle upload\" 2",
        "timed \"Remote install, Docker pull, optional smoke, cron\"",
        "REMOTE TIMING BEGIN",
        "trap remote_failure_diagnostics EXIT",
        "REMOTE FAILURE status=${status}; collecting deploy diagnostics",
        "docker compose version",
        "remote_timed \"Make deployment bundle readable\"",
        "remote_timed \"Extract deployment bundle as app user\"",
        "remote_timed \"Docker pull and optional smoke\"",
        "remote_timed \"Install cron schedule\"",
        "remote_timed \"Restart cron scheduler\"",
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
        "timeout --kill-after=10s 45s terraform -chdir=infra/gcp-free-tier output -raw",
        "timeout --kill-after=30s 2m tar",
        "docker-compose.vm.yml",
        "timeout-minutes: 40",
        "timeout-minutes: 90",
        "Remote install, Docker pull, optional smoke, cron",
    )
    for snippet in expected_snippets:
        assert snippet in workflow


def test_prebuilt_image_workflow_pushes_sha_and_optional_main_tag_with_cache() -> None:
    workflow = IMAGE_WORKFLOW.read_text(encoding="utf-8")

    expected_snippets = (
        # Triggered manually or called by other workflows; no push trigger (auto-cicd handles
        # build+deploy on PR and release events instead).
        "workflow_dispatch:",
        "workflow_call:",
        "permissions:",
        "packages: write",
        "docker/setup-buildx-action@v3",
        "docker/login-action@v3",
        "docker buildx build",
        '--build-arg "APP_UID=1000"',
        '--build-arg "APP_GID=1000"',
        "--push",
        # Immutable per-commit tag always pushed; :main only moved when requested.
        'tags=(--tag "${IMAGE}:${BUILD_SHA}")',
        'tags+=(--tag "${IMAGE}:main")',
        "--cache-from type=gha",
        "--cache-to type=gha,mode=max",
        # Exposes the pulled ref to callers and lets them target an arbitrary ref.
        "value: ${{ jobs.build.outputs.image }}",
        "ref: ${{ inputs.ref || github.sha }}",
    )
    for snippet in expected_snippets:
        assert snippet in workflow

    # No push trigger: image builds are always initiated by auto-cicd or workflow_dispatch.
    assert "push:" not in workflow
    assert "branches:" not in workflow


def test_deploy_polling_and_retries_are_bounded() -> None:
    workflow = DEPLOY_WORKFLOW.read_text(encoding="utf-8")
    upload_block = workflow.split("- name: Upload and install on VM", 1)[1].split("- name: Notify Telegram", 1)[0]

    assert "- name: Export Terraform outputs" in workflow
    assert "tf_output()" in workflow
    assert "Terraform output ${name} failed; retrying attempt ${attempt}/3 in 10s" in workflow
    assert "TF_INSTANCE_NAME=${instance_name}" in workflow
    assert "TF_ZONE=${zone}" in workflow
    assert "TF_STARTUP_REVISION=${startup_revision}" in workflow
    assert 'instance_name="${TF_INSTANCE_NAME:?missing Terraform output TF_INSTANCE_NAME}"' in upload_block
    assert 'zone="${TF_ZONE:?missing Terraform output TF_ZONE}"' in upload_block
    assert 'startup_revision="${TF_STARTUP_REVISION:?missing Terraform output TF_STARTUP_REVISION}"' in upload_block
    assert "terraform -chdir=infra/gcp-free-tier output -raw" not in upload_block
    assert 'wait_for_vm_ready 720 "12m"' in workflow
    assert "local deadline=$((SECONDS + wait_seconds)) attempt=0 status=1 saw_remote=0" in workflow
    assert "startup revision ${startup_revision} not ready within ${wait_label}" in workflow
    assert "startup_status=running" in workflow
    assert "not resetting a still-bootstrapping host" in workflow
    assert "startup_failed=$(cat /var/lib/poma/vm-startup-failed" in workflow
    assert "VM startup script failed before writing the readiness sentinel" in workflow
    assert "return 42" in workflow
    assert "exit 42" in workflow
    assert "id poma >/dev/null 2>&1" in workflow
    assert "systemctl is-active --quiet docker" in workflow
    assert "systemctl is-active --quiet cron" in workflow
    assert "sudo crontab -r -u poma" in workflow
    assert "sudo systemctl stop cron" in workflow
    assert "docker compose --env-file .compose.env -f docker-compose.vm.yml down --remove-orphans" in workflow
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
        "DOCKER_DIAGNOSTIC_TIMEOUT=\"${DOCKER_DIAGNOSTIC_TIMEOUT:-10s}\"",
        "timeout --kill-after=10s \"${DOCKER_DIAGNOSTIC_TIMEOUT}\" docker system df",
        "RUN_DOCKER_PULL_DIAGNOSTICS:-false",
        'bounded_docker_diagnostics "before image pull"',
        'bounded_docker_diagnostics "after image pull"',
        "timeout_compose 5m pull poma",
        "timeout_compose 3m run --rm",
        "smoke_session=\"deploy-smoke-$(date -u +%Y%m%dT%H%M%SZ)\"",
        "poma rebalance --session-date \"${smoke_session}\" --dry-run",
        "timed \"deploy smoke test\" run_deploy_smoke",
        "DOCKER_PRUNE_TIMEOUT=\"${DOCKER_PRUNE_TIMEOUT:-45s}\"",
        "timeout --kill-after=30s \"${DOCKER_PRUNE_TIMEOUT}\" docker image prune -f",
        "RUN_DOCKER_PRUNE:-true",
        "timed \"dangling image prune\" prune_dangling_images",
        "DEFAULT_IMAGE_TAG=\"${DEFAULT_IMAGE_TAG:-main}\"",
    )
    for snippet in expected_snippets:
        assert snippet in script

    assert "--session-date deploy-smoke --dry-run" not in script
    assert "docker compose build" not in script
    assert 'timeout --kill-after=30s "${duration}" docker compose' in script
    assert "docker system df || true" not in script
    assert "docker image prune -f >/dev/null\n}" not in script
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
    # click must be pinned to the 8.1.x line: typer 0.12.3 is incompatible with click >=8.2,
    # which otherwise crashes every `poma` command in the built image at command-build time.
    assert "click==8.1.8" in constraints
