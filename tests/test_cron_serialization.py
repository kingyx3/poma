from __future__ import annotations

from pathlib import Path


def test_scheduled_commands_are_serialized_before_starting_containers() -> None:
    script = Path("ops/cron/run_logged.sh").read_text()

    assert 'lock_path="${POMA_COMMAND_LOCK_PATH:-/opt/poma/state/poma-command.lock}"' in script
    assert 'flock -n 9' in script
    assert "command skipped: lock busy" in script
    assert script.index("flock -n 9") < script.index('"$@" 2>&1 | append_timestamped_output')
