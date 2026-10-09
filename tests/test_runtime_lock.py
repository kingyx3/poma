from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from conftest import make_settings
from typer.testing import CliRunner

from poma import cli
from poma.runtime_lock import RuntimeBusy, runtime_lock


def test_lock_excludes_other_processes_and_recovers_after_process_death(tmp_path: Path) -> None:
    child = subprocess.Popen(
        [sys.executable, '-c', '''
import sys
from pathlib import Path
from poma.runtime_lock import runtime_lock
with runtime_lock(Path(sys.argv[1])):
    print("locked", flush=True)
    sys.stdin.read()
''', str(tmp_path)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
    )
    try:
        assert child.stdout.readline().strip() == 'locked'
        with pytest.raises(RuntimeBusy), runtime_lock(tmp_path):
            pytest.fail('concurrent process acquired the state lock')
        # Independent state directories must not serialize unrelated environments.
        with runtime_lock(tmp_path / 'other'):
            pass
    finally:
        child.kill()
        child.communicate(timeout=10)
    with runtime_lock(tmp_path):
        pass


def test_exception_releases_lock_without_unlinking(tmp_path: Path) -> None:
    with pytest.raises(ValueError), runtime_lock(tmp_path):
        inode = (tmp_path / 'poma-runtime.lock').stat().st_ino
        raise ValueError('broker failure')
    with runtime_lock(tmp_path):
        assert (tmp_path / 'poma-runtime.lock').stat().st_ino == inode


@pytest.mark.parametrize(('command', 'exit_code'), [('rebalance', 75), ('monitor', 0), ('reconcile-orders', 0)])
def test_busy_cli_does_not_touch_state_or_broker(monkeypatch, tmp_path: Path, command: str, exit_code: int) -> None:
    monkeypatch.setattr(cli, 'get_settings', lambda: make_settings(STATE_DIR=tmp_path))

    def forbidden(*args, **kwargs):
        pytest.fail('busy command reached trading/state logic')

    monkeypatch.setattr(cli, '_run_rebalance', forbidden)
    monkeypatch.setattr(cli, 'LocalState', forbidden)
    monkeypatch.setattr(cli, 'build_broker', forbidden)
    with runtime_lock(tmp_path):
        result = CliRunner().invoke(cli.app, [command])
    assert result.exit_code == exit_code, result.output
    assert 'another POMA command' in result.output


def test_manual_rebalance_holds_lock_through_execution(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(cli, 'get_settings', lambda: make_settings(STATE_DIR=tmp_path))
    calls = []

    def run(**kwargs):
        calls.append(kwargs)
        with pytest.raises(RuntimeBusy), runtime_lock(tmp_path):
            pytest.fail('rebalance did not hold lock')

    monkeypatch.setattr(cli, '_run_rebalance', run)
    result = CliRunner().invoke(cli.app, ['rebalance', '--dry-run'])
    assert result.exit_code == 0, result.exception
    assert calls[0]['force_dry_run'] is True
    with runtime_lock(tmp_path):
        pass


def test_operator_repair_obeys_same_lock(monkeypatch, tmp_path: Path) -> None:
    from ops.scripts import resolve_unresolved_order as resolver

    monkeypatch.setattr(resolver, 'get_settings', lambda: make_settings(STATE_DIR=tmp_path))
    monkeypatch.setattr(resolver, '_resolve', lambda: pytest.fail('repair ran while state was locked'))
    with runtime_lock(tmp_path), pytest.raises(SystemExit, match='another POMA command'):
        resolver.main()
