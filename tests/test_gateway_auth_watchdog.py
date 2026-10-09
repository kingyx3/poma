from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
from conftest import make_settings
from typer.testing import CliRunner

from ops.scripts import gateway_auth_watchdog as watchdog
from ops.scripts import install_ibc_config_helper as installer
from ops.scripts import run_gateway_ops_workflow as ops
from poma import cli, gateway_auth
from poma.runtime_lock import runtime_lock


class AuthBroker:
    def __init__(self, accounts=('DU1',), connected=True, server_time='fresh'):
        self.accounts = accounts
        self.connected = connected
        self.server_time = server_time
        self.disconnected = False

    def managedAccounts(self):
        return self.accounts

    def isConnected(self):
        return self.connected

    def reqCurrentTime(self):
        return self.server_time

    def disconnect(self):
        self.disconnected = True


@pytest.mark.parametrize(('accounts', 'connected', 'server_time', 'expected'), [
    (('DU1',), True, 'fresh', 'authenticated'),
    ((), True, 'fresh', 'unavailable'),
    (('DU1',), False, 'fresh', 'unavailable'),
    (('DU1',), True, None, 'unavailable'),
    (('OTHER',), True, 'fresh', 'configuration_error'),
])
def test_auth_probe_requires_account_and_fresh_response(monkeypatch, accounts, connected, server_time, expected):
    broker = AuthBroker(accounts, connected, server_time)
    calls = []

    def connect(settings, **kwargs):
        calls.append((settings, kwargs))
        return broker

    monkeypatch.setattr(gateway_auth, '_connect_ib', connect)
    settings = make_settings(TRADING_MODE='paper', IBKR_ACCOUNT='DU1', IBKR_CONNECT_ATTEMPTS=5)
    assert gateway_auth.probe_authentication(settings).value == expected
    assert broker.disconnected
    assert calls[0][0].ibkr_connect_attempts == 1
    assert calls[0][1]['timeout'] == 60
    assert calls[0][1]['client_id'] != settings.ibkr_client_id
    # This fake deliberately has no order/what-if/market-data methods.


def test_auth_probe_handles_timeout_without_leaking_errors(monkeypatch):
    def connect(*args, **kwargs):
        raise TimeoutError('private broker error')
    monkeypatch.setattr(gateway_auth, '_connect_ib', connect)
    assert gateway_auth.probe_authentication(make_settings(TRADING_MODE='paper', IBKR_ACCOUNT='DU1')) == 'unavailable'


@pytest.mark.parametrize(('mode', 'account', 'expected'), [
    ('dry_run', 'DU1', 'disabled'), ('paper', '', 'configuration_error'),
])
def test_disabled_or_unconfigured_probe_never_connects(monkeypatch, mode, account, expected):
    monkeypatch.setattr(gateway_auth, '_connect_ib', lambda *a, **k: pytest.fail('unexpected connection'))
    assert gateway_auth.probe_authentication(make_settings(TRADING_MODE=mode, IBKR_ACCOUNT=account)) == expected


def test_cli_emits_safe_machine_readable_result(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, 'get_settings', lambda: make_settings(STATE_DIR=tmp_path))
    monkeypatch.setattr(cli, 'probe_authentication', lambda _: gateway_auth.AuthStatus.UNAVAILABLE)
    result = CliRunner().invoke(cli.app, ['gateway-auth-check'])
    assert result.exit_code == 10
    payload = json.loads(result.output)
    assert payload == {'status': 'unavailable', 'state_inode': tmp_path.stat().st_ino,
                       'state_device': tmp_path.stat().st_dev}


def test_cli_config_error_output_has_no_secret(monkeypatch):
    def invalid_settings():
        raise ValueError('secret=do-not-print')
    monkeypatch.setattr(cli, 'get_settings', invalid_settings)
    result = CliRunner().invoke(cli.app, ['gateway-auth-check'])
    assert result.exit_code == 20
    assert json.loads(result.output) == {'status': 'configuration_error'}


def test_notifications_use_fixed_recovery_messages(monkeypatch):
    sent = []
    monkeypatch.setattr(cli, 'get_settings', make_settings)
    monkeypatch.setattr(cli, 'send_alert', lambda settings, message: sent.append(message))
    result = CliRunner().invoke(cli.app, ['gateway-watchdog-notify', 'restart'])
    assert result.exit_code == 0
    assert 'Approve IBKR Mobile' in sent[0]
    assert CliRunner().invoke(cli.app, ['gateway-watchdog-notify', 'arbitrary-message']).exit_code != 0
    assert len(sent) == 1


def fail_until_threshold(state, now):
    for _ in range(watchdog.FAILURE_THRESHOLD - 1):
        assert watchdog.decide(state, 'unavailable', now) == (False, None)
    return watchdog.decide(state, 'unavailable', now)


def test_transient_failures_do_not_restart_and_healthy_resets_count():
    state = watchdog.State()
    watchdog.decide(state, 'unavailable', 10000)
    watchdog.decide(state, 'unavailable', 10120)
    assert watchdog.decide(state, 'authenticated', 10240) == (False, None)
    assert state.failures == 0
    assert watchdog.decide(state, 'unavailable', 10360) == (False, None)


def test_restart_cooldown_budget_and_recovery_alert():
    state = watchdog.State()
    assert fail_until_threshold(state, 10000) == (True, 'restart')
    assert fail_until_threshold(state, 10360) == (False, None)
    assert watchdog.decide(state, 'unavailable', 10900) == (True, 'restart')
    fail_until_threshold(state, 11800)
    assert len(state.restarts) == 2
    assert watchdog.decide(state, 'unavailable', 11801)[0] is False
    assert watchdog.decide(state, 'authenticated', 11802) == (False, 'recovered')
    assert watchdog.decide(state, 'authenticated', 11803) == (False, None)
    # Recovery does not clear rate limits for a flapping session.
    assert len(state.restarts) == 2
    fail_until_threshold(state, 11804)
    assert len(state.restarts) == 2
    assert watchdog.decide(state, 'unavailable', 13601) == (True, 'restart')


def test_wrong_account_alerts_but_never_restarts():
    state = watchdog.State()
    assert watchdog.decide(state, 'configuration_error', 10000) == (False, 'configuration')
    assert watchdog.decide(state, 'configuration_error', 10120) == (False, None)
    assert watchdog.decide(state, 'configuration_error', 10900) == (False, 'configuration')
    assert not state.restarts


def test_state_survives_watchdog_restart_and_corruption_fails_closed(tmp_path):
    path = tmp_path / 'state.json'
    state = watchdog.State()
    fail_until_threshold(state, 10000)
    watchdog.save_state(path, state)
    assert watchdog.load_state(path) == state
    assert fail_until_threshold(watchdog.load_state(path), 10200)[0] is False
    path.write_text('{truncated')
    with pytest.raises(ValueError):
        watchdog.load_state(path)


@pytest.mark.parametrize('payload', [
    {'failures': -1}, {'failures': True}, {'last_alert': float('nan')}, {'restarts': [float('inf')]},
])
def test_invalid_policy_state_is_rejected(tmp_path, payload):
    path = tmp_path / 'state.json'
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError):
        watchdog.load_state(path)


def result_for(status, state_dir, code=None):
    codes = {'authenticated': 0, 'unavailable': 10, 'configuration_error': 20, 'disabled': 0}
    return subprocess.CompletedProcess([], codes.get(status, 1) if code is None else code,
        json.dumps({'status': status, 'state_inode': state_dir.stat().st_ino,
                    'state_device': state_dir.stat().st_dev}), '')


@pytest.mark.parametrize('status', ['authenticated', 'unavailable', 'disabled', 'configuration_error'])
def test_host_accepts_only_matching_probe_contract(tmp_path, status):
    assert watchdog.authentication_status(result_for(status, tmp_path), tmp_path) == status
    assert watchdog.authentication_status(result_for(status, tmp_path, 99), tmp_path) == 'probe_error'


def test_docker_failure_or_wrong_state_volume_cannot_trigger_restart(tmp_path):
    result = subprocess.CompletedProcess([], 1, 'docker error', '')
    assert watchdog.authentication_status(result, tmp_path) == 'probe_error'
    other = tmp_path / 'other'
    other.mkdir()
    assert watchdog.authentication_status(result_for('unavailable', other), tmp_path) == 'configuration_error'


@pytest.fixture
def installed_watchdog(tmp_path, monkeypatch):
    for name in ('.env', '.compose.env', 'docker-compose.vm.yml', 'ibc.ini'):
        (tmp_path / name).write_text('present')
    (tmp_path / 'state').mkdir()
    monkeypatch.setattr(watchdog, 'APP_DIR', tmp_path)
    monkeypatch.setattr(watchdog, 'IBC_CONFIG', tmp_path / 'ibc.ini')
    monkeypatch.setattr(watchdog, 'STATE_PATH', tmp_path / 'policy.json')
    monkeypatch.setattr(watchdog.time, 'monotonic', lambda: 10000)
    monkeypatch.setattr(watchdog.time, 'time', lambda: 10000)
    commands = []

    def run(command, timeout=30):
        commands.append(command)
        return subprocess.CompletedProcess(command, 0,
            'ActiveState=active\nActiveEnterTimestampMonotonic=1000000\n', '')

    monkeypatch.setattr(watchdog, 'run', run)
    return tmp_path, commands


def test_watchdog_restart_is_durable_and_holds_trading_locks(installed_watchdog, monkeypatch):
    path, commands = installed_watchdog
    alerts = []
    state = watchdog.State(failures=2)
    watchdog.save_state(watchdog.STATE_PATH, state)

    def compose(command, timeout):
        with pytest.raises(RuntimeError), runtime_lock(path / 'state'):
            pytest.fail('watchdog released lock before completing recovery')
        if command[0] == 'gateway-auth-check':
            return result_for('unavailable', path / 'state')
        alerts.append(command)
        # Attempt is already saved even if service or alert delivery fails later.
        assert watchdog.load_state(watchdog.STATE_PATH).restarts == [10000]
        return subprocess.CompletedProcess(command, 0, '', '')

    monkeypatch.setattr(watchdog, 'compose', compose)
    assert watchdog.main() == 0
    assert ['systemctl', 'restart', 'ibgateway'] in commands
    assert alerts == [['gateway-watchdog-notify', 'restart']]
    with runtime_lock(path / 'state'):
        pass
    assert (path / 'state' / 'poma-runtime.lock').stat().st_uid == (path / 'state').stat().st_uid


def test_watchdog_skips_busy_state_without_probe(installed_watchdog, monkeypatch):
    path, commands = installed_watchdog
    monkeypatch.setattr(watchdog, 'compose', lambda *a, **k: pytest.fail('probed busy state'))
    with runtime_lock(path / 'state'):
        assert watchdog.main() == 0
    assert ['systemctl', 'restart', 'ibgateway'] not in commands


def test_watchdog_does_not_reset_gateway_for_docker_failure(installed_watchdog, monkeypatch):
    _, commands = installed_watchdog
    monkeypatch.setattr(watchdog, 'compose', lambda *a, **k: subprocess.CompletedProcess([], 1, 'docker error', ''))
    assert watchdog.main() == 1
    assert ['systemctl', 'restart', 'ibgateway'] not in commands


def test_watchdog_honors_manual_stop_and_startup_grace(installed_watchdog, monkeypatch):
    monkeypatch.setattr(watchdog, 'compose', lambda *a, **k: pytest.fail('probed during stopped/startup state'))
    for output in ['ActiveState=inactive\nActiveEnterTimestampMonotonic=1\n',
                   'ActiveState=active\nActiveEnterTimestampMonotonic=9900000000\n']:
        monkeypatch.setattr(watchdog, 'run',
                            lambda *a, output=output, **k: subprocess.CompletedProcess([], 0, output, ''))
        assert watchdog.main() == 0


def test_compose_timeout_cleans_up_read_only_container(monkeypatch):
    calls = []

    def run(command, timeout=30):
        calls.append(command)
        if command[:2] == ['docker', 'compose']:
            raise subprocess.TimeoutExpired(command, timeout)
        return subprocess.CompletedProcess(command, 0, '', '')

    monkeypatch.setattr(watchdog, 'run', run)
    with pytest.raises(subprocess.TimeoutExpired):
        watchdog.compose(['gateway-auth-check'], timeout=90)
    assert calls[0] == calls[-1] == ['docker', 'rm', '-f', watchdog.CONTAINER_NAME]


def test_runtime_restart_time_is_valid_and_repairs_existing_config(tmp_path):
    script = Path('ops/scripts/ensure_ibgateway_service.sh').read_text()
    engine = script.split("<<'ENGINE'", 1)[1].split('\nENGINE\n', 1)[0]
    setter = engine.split('set_ini() {', 1)[1].split('\napi_port_open()', 1)[0]
    config = tmp_path / 'config.ini'
    config.write_text('AutoRestartTime=23:45\nIbPassword=retained\n')
    bash = 'set -eu\nCONFIG="$1"\nLOGIN_DIALOG_DISPLAY_TIMEOUT=240\nlog() { :; }\nset_ini() {' + setter
    subprocess.run(['bash', '-c', bash + '\nensure_runtime_config', 'test', str(config)],
                   env={**os.environ, 'TMPDIR': str(tmp_path)}, check=True)
    assert 'AutoRestartTime=11:45 PM' in config.read_text()
    assert 'IbPassword=retained' in config.read_text()
    assert 'truncate -s' not in engine
    assert 'set_ini AutoRestartTime "11:45 PM"' in installer.CONFIG_HELPER_TEXT


def test_gateway_ops_resumes_watchdog_even_after_configure_failure(monkeypatch):
    values = dict(DEPLOY_ENVIRONMENT='dev', INPUT_ACTION='restart', GCP_PROJECT_ID='project',
                  GCP_ZONE='zone', GCP_VM_NAME='vm')
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    commands = []

    def run(command, **kwargs):
        commands.append(command)
        return 1 if command[-1] == 'sudo systemctl restart ibgateway' else 0

    monkeypatch.setattr(ops, 'run', run)
    assert ops.main() == 1
    assert 'stop poma-gateway-watchdog.timer' in str(commands)
    assert 'start poma-gateway-watchdog.timer' in commands[-1][-1]
    assert 'ops/scripts/gateway_auth_watchdog.py' in ops.HELPER_SCRIPTS
    assert 'OnUnitInactiveSec=2min' in installer.WATCHDOG_TIMER_TEXT


def test_check_only_verifies_after_startup_without_policy_or_restart(installed_watchdog, monkeypatch):
    path, commands = installed_watchdog
    monkeypatch.setattr(watchdog, 'compose', lambda *a, **k: result_for('authenticated', path / 'state'))
    monkeypatch.setattr(watchdog.time, 'monotonic', lambda: 100)
    assert watchdog.main(check_only=True) == 0
    assert not watchdog.STATE_PATH.exists()
    assert ['systemctl', 'restart', 'ibgateway'] not in commands


def test_restart_timeout_still_alerts_and_retains_budget(installed_watchdog, monkeypatch):
    path, _ = installed_watchdog
    watchdog.save_state(watchdog.STATE_PATH, watchdog.State(failures=2))
    original_run = watchdog.run
    events = []

    def run(command, timeout=30):
        if command == ['systemctl', 'restart', 'ibgateway']:
            raise subprocess.TimeoutExpired(command, timeout)
        return original_run(command, timeout)

    def compose(command, timeout):
        if command[0] == 'gateway-auth-check':
            return result_for('unavailable', path / 'state')
        events.append(command[-1])
        return subprocess.CompletedProcess(command, 0, '', '')

    monkeypatch.setattr(watchdog, 'run', run)
    monkeypatch.setattr(watchdog, 'compose', compose)
    assert watchdog.main() == 0
    assert events == ['restart_failed']
    assert watchdog.load_state(watchdog.STATE_PATH).restarts == [10000]
