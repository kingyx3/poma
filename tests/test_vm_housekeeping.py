"""VM housekeeping must reclaim only disposable resources and preserve active state."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "ops/scripts/vm_housekeeping.sh"
IMAGE = "ghcr.io/kingyx3/poma:"
PINNED = IMAGE + "a" * 40
ROLLBACK = IMAGE + "b" * 40
STALE = IMAGE + "c" * 40


def _fake_host(tmp_path: Path, *, disk_full: bool) -> tuple[dict[str, str], Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "actions.log"
    recovered = tmp_path / "recovered"
    (tmp_path / ".compose.env").write_text(f"POMA_IMAGE={PINNED}\n")
    (bin_dir / "docker").write_text(
        "#!/usr/bin/env bash\n"
        'echo "docker $*" >> "$MOCK_ACTIONS"\n'
        'if [[ "$1" == "image" && "$2" == "ls" ]]; then\n'
        '  printf "%s\\n" "$MOCK_PINNED" "$MOCK_ROLLBACK" "$MOCK_STALE"\n'
        'fi\n'
        'if [[ "$1" == "image" && "$2" == "rm" ]]; then touch "$MOCK_RECOVERED"; fi\n'
        "exit 0\n"
    )
    (bin_dir / "df").write_text(
        "#!/usr/bin/env bash\n"
        'if [[ "$1" == "-Pk" ]]; then\n'
        '  echo "Filesystem 1024-blocks Used Available Capacity Mounted on"\n'
        '  if [[ "$MOCK_DISK_FULL" == "true" && ! -e "$MOCK_RECOVERED" ]]; then\n'
        '    echo "/dev/root 30000000 29900000 100000 99% /"\n'
        "  else\n"
        '    echo "/dev/root 30000000 20000000 10000000 66% /"\n'
        "  fi\n"
        "else\n"
        '  echo "/dev/root 30G 20G 10G 66% /"\n'
        "fi\n"
    )
    for name in ("apt-get", "journalctl", "logrotate"):
        (bin_dir / name).write_text(
            "#!/usr/bin/env bash\n"
            f'echo "{name} $*" >> "$MOCK_ACTIONS"\n'
            "exit 0\n"
        )
    for path in bin_dir.iterdir():
        path.chmod(0o755)
    env = {
        **os.environ,
        "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"],
        "POMA_APP_DIR": str(tmp_path),
        "MOCK_ACTIONS": str(log),
        "MOCK_RECOVERED": str(recovered),
        "MOCK_DISK_FULL": str(disk_full).lower(),
        "MOCK_PINNED": PINNED,
        "MOCK_ROLLBACK": ROLLBACK,
        "MOCK_STALE": STALE,
    }
    return env, log


@pytest.mark.parametrize("mode", ["daily", "predeploy"])
def test_housekeeping_preserves_pinned_rollback_and_volumes(tmp_path: Path, mode: str) -> None:
    env, actions = _fake_host(tmp_path, disk_full=True)
    result = subprocess.run(
        ["bash", str(SCRIPT), mode, "dev"], env=env,
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    commands = actions.read_text()
    assert "docker image rm " + STALE in commands
    assert "docker image rm " + PINNED not in commands
    assert "docker image rm " + ROLLBACK not in commands
    assert "--volumes" not in commands
    assert "docker builder prune" in commands
    assert "journalctl --vacuum-size=100M" in commands
    assert "docker container prune" in commands


def test_predeploy_skips_expensive_cleanup_with_sufficient_disk(tmp_path: Path) -> None:
    env, actions = _fake_host(tmp_path, disk_full=False)
    result = subprocess.run(
        ["bash", str(SCRIPT), "predeploy", "dev"], env=env,
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "no forced pruning" in result.stdout
    assert not actions.exists()


def test_housekeeping_rejects_invalid_environment(tmp_path: Path) -> None:
    env, _ = _fake_host(tmp_path, disk_full=True)
    result = subprocess.run(
        ["bash", str(SCRIPT), "predeploy", "production-ish"], env=env,
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 2


def test_non_dev_housekeeping_never_discards_sha_tagged_rollback_images(tmp_path: Path) -> None:
    env, actions = _fake_host(tmp_path, disk_full=True)
    result = subprocess.run(
        ["bash", str(SCRIPT), "daily", "prd"], env=env,
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "docker image rm " not in actions.read_text()
