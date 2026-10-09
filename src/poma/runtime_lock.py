"""Single-writer guard shared by CLI commands and operator repairs on one host."""

from __future__ import annotations

import fcntl
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


class RuntimeBusy(RuntimeError):
    """Another process is using the runtime state and broker lifecycle."""


@contextmanager
def runtime_lock(state_dir: Path) -> Iterator[None]:
    state_dir.mkdir(parents=True, exist_ok=True)
    # Separate from cron's outer lock: containers inherit the state volume, not
    # the host wrapper's descriptor. Never unlink this file (that splits locks).
    with (state_dir / "poma-runtime.lock").open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeBusy("another POMA command is using this STATE_DIR; retry after it finishes") from exc
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)
