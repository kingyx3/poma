#!/usr/bin/env python3
"""Root timer: check authenticated API access and rate-limit safe login recovery.

Only the deployed app reads its secrets. No broker orders/market data are requested.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import math
import os
import subprocess
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path

APP_DIR = Path('/opt/poma')
STATE_PATH = Path('/var/lib/poma/gateway-watchdog.json')
IBC_CONFIG = Path('/home/poma/ibc/config.ini')
CONTAINER_NAME = 'poma-gateway-auth-watchdog'
FAILURE_THRESHOLD = 3
STARTUP_GRACE_SECONDS = 360
RESTART_COOLDOWN_SECONDS = 900
RESTART_WINDOW_SECONDS = 3600
MAX_RESTARTS_PER_WINDOW = 2
ALERT_INTERVAL_SECONDS = 900


@dataclass
class State:
    failures: int = 0
    incident: bool = False
    last_alert: float = 0
    restarts: list[float] = field(default_factory=list)


def decide(state: State, status: str, now: float) -> tuple[bool, str | None]:
    """Update durable policy BEFORE performing a restart, including failed restarts."""
    state.restarts = [stamp for stamp in state.restarts if now - stamp < RESTART_WINDOW_SECONDS]
    if status == 'authenticated':
        event = 'recovered' if state.incident else None
        state.failures = 0
        state.incident = False
        return False, event
    if status == 'disabled':
        state.failures = 0
        state.incident = False
        return False, None
    if status == 'configuration_error':
        state.failures = 0
        event = 'configuration' if not state.incident or now - state.last_alert >= ALERT_INTERVAL_SECONDS else None
        state.incident = True
        if event:
            state.last_alert = now
        return False, event
    if status != 'unavailable':
        raise ValueError('unknown authentication status')
    state.failures += 1
    if state.failures < FAILURE_THRESHOLD:
        return False, None
    state.incident = True
    cooldown = bool(state.restarts and now - state.restarts[-1] < RESTART_COOLDOWN_SECONDS)
    if cooldown or len(state.restarts) >= MAX_RESTARTS_PER_WINDOW:
        event = 'waiting' if now - state.last_alert >= ALERT_INTERVAL_SECONDS else None
        if event:
            state.last_alert = now
        return False, event
    state.restarts.append(now)
    state.failures = 0
    state.incident = True
    state.last_alert = now
    return True, 'restart'


def load_state(path: Path) -> State:
    if not path.exists():
        return State()
    data = json.loads(path.read_text())
    state = State(**data)
    if type(state.failures) is not int or state.failures < 0 or type(state.incident) is not bool:
        raise ValueError('invalid watchdog state')
    stamps = [state.last_alert, *state.restarts]
    if any(type(stamp) not in {int, float} or not math.isfinite(stamp) or stamp < 0 for stamp in stamps):
        raise ValueError('invalid watchdog timestamps')
    return state


def save_state(path: Path, state: State) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix='.gateway-watchdog-')
    try:
        with os.fdopen(fd, 'w') as handle:
            json.dump(asdict(state), handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        Path(name).unlink(missing_ok=True)


@contextmanager
def command_locks(state_dir: Path) -> Iterator[None]:
    # Both the cron host wrapper and direct CLI commands must be idle. Lock files
    # created by root inherit the mounted state directory's app ownership.
    descriptors = []
    owner = state_dir.stat()
    try:
        for name in ('poma-command.lock', 'poma-runtime.lock'):
            fd = os.open(state_dir / name, os.O_CREAT | os.O_RDWR, 0o600)
            descriptors.append(fd)
            os.fchown(fd, owner.st_uid, owner.st_gid)
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        for fd in reversed(descriptors):
            os.close(fd)


def run(command: list[str], timeout: int = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, text=True, capture_output=True, timeout=timeout, check=False)


def compose(command: list[str], timeout: int) -> subprocess.CompletedProcess[str]:
    # A timed-out docker client can leave a container alive. Its fixed name is
    # exclusively reserved for this read-only watchdog and cleaned on every tick.
    prefix = ['docker', 'compose', '--project-directory', str(APP_DIR),
              '--env-file', str(APP_DIR / '.compose.env'), '-f', str(APP_DIR / 'docker-compose.vm.yml')]
    run(['docker', 'rm', '-f', CONTAINER_NAME], timeout=15)
    try:
        return run(prefix + ['run', '--rm', '--no-deps', '--name', CONTAINER_NAME, 'poma', *command], timeout)
    finally:
        run(['docker', 'rm', '-f', CONTAINER_NAME], timeout=15)


def authentication_status(result: subprocess.CompletedProcess[str], state_dir: Path) -> str:
    # Docker/CLI crashes and unknown commands are NOT authentication failures.
    # Never restart Gateway because an app upgrade or docker daemon is broken.
    try:
        payload = json.loads(result.stdout.strip().splitlines()[-1])
        status = payload['status']
        expected_code = {'authenticated': 0, 'disabled': 0, 'unavailable': 10, 'configuration_error': 20}[status]
        if result.returncode != expected_code:
            return 'probe_error'
        if status != 'configuration_error':
            mounted = state_dir.stat()
            if (payload.get('state_inode'), payload.get('state_device')) != (mounted.st_ino, mounted.st_dev):
                return 'configuration_error'
        return status
    except (ValueError, IndexError, KeyError, TypeError, AttributeError):
        return 'probe_error'


def tick(*, check_only: bool = False) -> int:
    state_dir = APP_DIR / 'state'
    if not all(path.is_file() for path in (APP_DIR / '.env', APP_DIR / '.compose.env',
                                          APP_DIR / 'docker-compose.vm.yml', IBC_CONFIG)) or not state_dir.is_dir():
        print('Watchdog skipped: configured app/Gateway is not installed.')
        return 0
    service = run(['systemctl', 'show', 'ibgateway', '--property=ActiveState',
                   '--property=ActiveEnterTimestampMonotonic'])
    props = dict(line.split('=', 1) for line in service.stdout.splitlines() if '=' in line)
    if service.returncode != 0 or props.get('ActiveState') != 'active':
        print('Watchdog skipped: Gateway is stopped or starting; systemd owns process recovery.')
        return 0
    try:
        age = time.monotonic() - int(props['ActiveEnterTimestampMonotonic']) / 1_000_000
    except (ValueError, KeyError):
        print('Watchdog skipped: cannot verify Gateway startup age.')
        return 1
    if not check_only and age < STARTUP_GRACE_SECONDS:
        print('Watchdog skipped: Gateway startup/mobile approval grace period.')
        return 0
    try:
        with command_locks(state_dir):
            status = authentication_status(compose(['gateway-auth-check'], timeout=150), state_dir)
            if check_only:
                print(f'Gateway watchdog verification: {status}')
                return 0 if status in {'authenticated', 'disabled'} else 1
            state = load_state(STATE_PATH)
            if status == 'probe_error':
                # Break consecutive auth failures across app/transport failures.
                state.failures = 0
                save_state(STATE_PATH, state)
                print('Watchdog probe failed at app/Docker layer; no Gateway restart.')
                return 1
            restart, event = decide(state, status, time.time())
            save_state(STATE_PATH, state)
            print(f'Gateway authentication: {status}; consecutive failures={state.failures}; restart={restart}')
            if restart:
                try:
                    restarted = run(['systemctl', 'restart', 'ibgateway'], timeout=45)
                    if restarted.returncode != 0:
                        event = 'restart_failed'
                except subprocess.TimeoutExpired:
                    event = 'restart_failed'
            if event:
                compose(['gateway-watchdog-notify', event], timeout=30)
            return 0
    except BlockingIOError:
        print('Watchdog skipped: trading/maintenance command holds the state lock.')
        return 0


def main(*, check_only: bool = False) -> int:
    try:
        return tick(check_only=check_only)
    except (OSError, ValueError, TypeError, subprocess.TimeoutExpired):
        # Never print exception text: docker or configuration output may contain secrets.
        print('Watchdog failed safely; inspect service/app configuration. No further restart attempted.')
        return 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check-only', action='store_true',
                        help='Verify authenticated access without recovery/state updates.')
    raise SystemExit(main(check_only=parser.parse_args().check_only))
