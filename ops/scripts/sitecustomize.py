from __future__ import annotations

import subprocess
from collections.abc import Sequence
from typing import Any

_ORIGINAL_RUN = subprocess.run


def _command_text(command: Any) -> str:
    if isinstance(command, str):
        return command
    if isinstance(command, Sequence):
        return " ".join(str(part) for part in command)
    return ""


def _visible_progress_command(command: Any) -> list[str] | None:
    if not isinstance(command, list):
        return None
    if "--command" not in command:
        return None
    index = command.index("--command")
    visible = list(command)
    visible[index + 1] = "sudo poma-diagnose-ibgateway progress --log-lines 80 || true"
    return visible


def _patched_run(*popenargs: Any, **kwargs: Any) -> subprocess.CompletedProcess[Any]:
    completed = _ORIGINAL_RUN(*popenargs, **kwargs)
    command = popenargs[0] if popenargs else kwargs.get("args")
    command_text = _command_text(command)
    if completed.returncode == 2 and "poma-diagnose-ibgateway startup-check" in command_text:
        print("::endgroup::")
        print("===== Visible gateway startup diagnostic =====")
        print("VISIBLE_STARTUP_CHECK_STATUS=failed")
        visible_command = _visible_progress_command(command)
        if visible_command is not None:
            _ORIGINAL_RUN(visible_command, check=False, text=True, timeout=120)
    return completed


subprocess.run = _patched_run
