"""Small durable Telegram outbox shared by the VM host and POMA container.

No tokens, chat IDs or API URLs are stored. The host cron drains the mounted
state volume; message delivery is at-least-once if Telegram accepts a request
but the acknowledgement is lost. This module never places broker orders.
"""
from __future__ import annotations

import json
import os
import random
import time
import uuid
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path

MAX_MESSAGES = 64
MAX_MESSAGE_LENGTH = 3900
TTL_SECONDS = 24 * 3600
MAX_ATTEMPTS = 8
INITIAL_RETRY_SECONDS = 60
BATCH_SIZE = 3


def _directory(state_dir: Path) -> Path:
    return state_dir / "telegram-outbox"


def _write(path: Path, value: dict) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            os.chmod(temporary, 0o600)
            json.dump(value, handle)
        os.replace(temporary, path)
    finally:
        with suppress(OSError):
            temporary.unlink(missing_ok=True)


def _messages(directory: Path) -> list[Path]:
    return sorted(directory.glob("*.json"))


def enqueue(state_dir: Path, text: str, *, now: float | None = None) -> bool:
    """Best effort: never let an alert-storage error fail a trading command."""
    try:
        now = time.time() if now is None else now
        directory = _directory(state_dir)
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        paths = _messages(directory)
        if len(paths) >= MAX_MESSAGES:
            # Keep already queued critical messages; bounded disk usage is essential.
            return False
        message = {
            "created_at": now,
            "next_attempt_at": now + INITIAL_RETRY_SECONDS,
            "attempts": 0,
            "text": text[:MAX_MESSAGE_LENGTH],
        }
        path = directory / f"{int(now * 1000):013d}-{uuid.uuid4().hex}.json"
        _write(path, message)
        return True
    except (OSError, ValueError, TypeError):
        return False


def drain(
    state_dir: Path,
    deliver: Callable[[str], bool],
    *,
    now: float | None = None,
    batch_size: int = BATCH_SIZE,
) -> tuple[int, int]:
    """Drain a bounded batch; retain failed items with capped jittered backoff.

    The cron wrapper is already serialized, but a separate flock prevents
    concurrent manual drains. All failures remain non-fatal to trading.
    """
    try:
        import fcntl

        now = time.time() if now is None else now
        directory = _directory(state_dir)
        if not directory.exists():
            return 0, 0
        with (directory / ".drain.lock").open("a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            delivered = 0
            attempted = 0
            for path in _messages(directory):
                try:
                    record = json.loads(path.read_text(encoding="utf-8"))
                    created = float(record["created_at"])
                    attempts = int(record["attempts"])
                    due = float(record["next_attempt_at"])
                    message = str(record["text"])
                    if now - created >= TTL_SECONDS or attempts >= MAX_ATTEMPTS:
                        path.unlink(missing_ok=True)
                        continue
                    if due > now or attempted >= batch_size:
                        continue
                    attempted += 1
                    if deliver(message):
                        path.unlink(missing_ok=True)
                        delivered += 1
                        continue
                    attempts += 1
                    if attempts >= MAX_ATTEMPTS:
                        path.unlink(missing_ok=True)
                        continue
                    delay = min(3600, INITIAL_RETRY_SECONDS * (2 ** attempts))
                    record["attempts"] = attempts
                    record["next_attempt_at"] = now + delay * random.uniform(0.8, 1.2)
                    _write(path, record)
                except (OSError, ValueError, KeyError, TypeError):
                    # Do not send corrupt or unknown-content messages.
                    with suppress(OSError):
                        path.unlink(missing_ok=True)
            return delivered, attempted
    except (OSError, ValueError, TypeError, BlockingIOError):
        return 0, 0
