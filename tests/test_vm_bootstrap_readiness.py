from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
STARTUP_SCRIPT = REPO_ROOT / "infra/gcp-free-tier/startup.sh"
CRON_SCHEDULE = REPO_ROOT / "ops/cron/poma.cron"
CRON_RUNNER = REPO_ROOT / "ops/cron/run_cron_job.sh"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_startup_quiesces_stale_cron_before_bootstrap_work() -> None:
    startup = _text(STARTUP_SCRIPT)
    quiesce_block = startup.split('log_phase "quiesce-cron"', 1)[1].split('log_phase "user"', 1)[0]

    assert "systemctl stop cron || true" in quiesce_block
    assert "crontab -u ubuntu -r || true" in quiesce_block
    assert 'crontab -u "$${APP_USER}" -r || true' in startup
    assert "pkill -f 'docker compose.*poma' || true" in quiesce_block


def test_startup_logs_phases_and_skips_apt_when_runtime_is_present() -> None:
    startup = _text(STARTUP_SCRIPT)

    for phase in (
        "quiesce-cron",
        "user",
        "runtime-dirs",
        "swap",
        "apt-update",
        "apt-install",
        "docker",
        "services",
        "ready",
    ):
        assert f'log_phase "{phase}"' in startup

    assert "apt packages already present; skipping apt-get update/install" in startup
    assert "! command -v docker" in startup
    assert "apt-get update" in startup


def test_cron_schedule_uses_guarded_runner_instead_of_raw_docker_compose() -> None:
    cron = _text(CRON_SCHEDULE)
    runner = _text(CRON_RUNNER)

    assert "bash ops/cron/run_cron_job.sh monitor" in cron
    assert "bash ops/cron/run_cron_job.sh reconcile-orders" in cron
    assert "/usr/bin/docker compose" not in cron

    assert "Missing runtime dependency" in runner
    assert "READY_SENTINEL" in runner
    assert 'case "${command_name}" in' in runner
    assert "monitor|reconcile-orders" in runner
    assert 'exec "${DOCKER_BIN}" compose' in runner
