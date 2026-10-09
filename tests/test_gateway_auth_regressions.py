import os
import subprocess
from types import SimpleNamespace

import pytest

from ops.scripts import install_ibc_config_helper as installer
from ops.scripts import run_gateway_ops_workflow as runner
from ops.scripts import wait_ib_gateway_2fa as auth


@pytest.fixture
def log_files(tmp_path, monkeypatch):
    log_dir = tmp_path / 'logs'
    log_dir.mkdir()
    monkeypatch.setattr(auth, 'LOG_PATHS', (log_dir,))
    monkeypatch.setattr(auth, 'CHECKPOINT_PATH', tmp_path / 'checkpoint.json')
    return log_dir


def test_checkpoint_ignores_old_auth_and_preserves_diagnostics(log_files):
    path = log_files / 'ibc.log'
    old = 'detected dialog entitled: Second Factor Authentication\n'
    path.write_text(old)
    assert auth.checkpoint_logs() == 0
    assert path.read_text() == old
    assert auth.tail_log_text(80) == ''
    with path.open('a') as handle:
        handle.write('new startup\n')
    assert auth.tail_log_text(80) == 'new startup'


def test_checkpoint_handles_rotation(log_files):
    path = log_files / 'ibc.log'
    path.write_text('old\n' * 50)
    auth.checkpoint_logs()
    path.unlink()
    path.write_text('detected dialog entitled: Second Factor Authentication\n')
    assert auth.TWO_FA_HINTS.search(auth.tail_log_text(80))


@pytest.mark.parametrize('text', [
    'ReloginAfterSecondFactorAuthenticationTimeout=yes',
    'SecondFactorAuthenticationExitInterval=60',
    'Notification service started',
    'Approve IBKR Mobile if prompted.',
])
def test_settings_and_generic_notifications_are_not_challenges(text):
    assert not auth.TWO_FA_HINTS.search(text)


def test_resumed_authenticated_socket_proceeds_to_required_api_check(log_files, monkeypatch):
    auth.checkpoint_logs()
    monkeypatch.setattr(auth, 'run', lambda *a, **kw: SimpleNamespace(
        stdout='STARTUP_STAGE=api-socket-open\nSTARTUP_ACTION=ready\n', returncode=0
    ))
    assert auth.wait_for_2fa(timeout_seconds=1, poll_seconds=1, log_lines=80, fail_no_progress_after=1) == 0


def test_broad_diagnostic_classification_cannot_claim_fresh_2fa(log_files, monkeypatch):
    auth.checkpoint_logs()
    times = iter([0, 0, 0, 2])
    monkeypatch.setattr(auth.time, 'monotonic', lambda: next(times))
    monkeypatch.setattr(auth.time, 'sleep', lambda _: None)
    monkeypatch.setattr(auth, 'print_progress', lambda _: None)
    monkeypatch.setattr(auth, 'run', lambda *a, **kw: SimpleNamespace(
        stdout='STARTUP_STAGE=login-reached-2fa-pending\nSTARTUP_ACTION=continue\n', returncode=0
    ))
    assert auth.wait_for_2fa(timeout_seconds=1, poll_seconds=1, log_lines=80, fail_no_progress_after=1) == 1


@pytest.mark.parametrize('environment,mode', [('prd', 'live'), ('dev', 'paper'), ('stg', 'paper')])
def test_market_data_verification_uses_environment_mode(monkeypatch, environment, mode):
    values = dict(DEPLOY_ENVIRONMENT=environment, INPUT_ACTION='verify-market-data',
                  GCP_PROJECT_ID='project', GCP_ZONE='zone', GCP_VM_NAME='vm')
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    commands = []
    monkeypatch.setattr(runner, 'run', lambda command, **kw: commands.append(command) or 0)
    assert runner.main() == 0
    assert f'TRADING_MODE={mode}' in commands[-1][-1]
    assert not any('systemctl restart' in str(command) for command in commands)


def test_ini_credentials_preserve_backslashes_and_whitespace(tmp_path):
    # Execute the actual installed helper's setter, without privileged install/service work.
    setter = installer.CONFIG_HELPER_TEXT.split('set_ini() {', 1)[1].split('\nset_ini IbLoginId', 1)[0]
    script = 'set -eu\nIBC_CONFIG="$1"\nset_ini() {' + setter + '\nset_ini IbPassword "$TEST_PASSWORD"\n'
    config = tmp_path / 'config.ini'
    config.write_text('IbPassword=old\n')
    secret = ' spaces \\n \\t \\123 $literal & end '
    subprocess.run(['bash', '-c', script, 'test', str(config)],
                   env={**os.environ, 'TEST_PASSWORD': secret, 'TMPDIR': str(tmp_path)}, check=True)
    assert config.read_text() == f'IbPassword={secret}\n'
