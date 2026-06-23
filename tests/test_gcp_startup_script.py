from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
STARTUP_SCRIPT = REPO_ROOT / "infra/gcp-free-tier/startup.sh"


def _startup() -> str:
    return STARTUP_SCRIPT.read_text(encoding="utf-8")


def test_ib_gateway_runs_under_systemd_foreground_process() -> None:
    script = _startup()

    assert "cat >/etc/systemd/system/ibgateway.service" in script
    assert "ExecStart=/usr/local/bin/poma-run-ib-gateway" in script
    assert 'exec "$${IBC_DIR}/gatewaystart.sh" -inline' in script
    assert 'exec "$${IB_GATEWAY_DIR}/ibgateway"' in script
    assert "Restart=always" in script


def test_ibc_uses_vm_local_config_and_gateway_install() -> None:
    script = _startup()

    assert 'IBC_INI": "/home/poma/ibc/config.ini"' in script
    assert 'TWS_PATH": tws_path' in script
    assert 'TWS_SETTINGS_PATH": "/home/poma/Jts"' in script
    assert 'LOG_PATH": "/home/poma/ibc/logs"' in script
    assert "find \"$${IB_GATEWAY_DIR}\" -type d -path '*/ibgateway/[0-9]*/jars'" in script


def test_ibc_credentials_remain_vm_local_and_private() -> None:
    script = _startup()

    assert "install -d -m 700 -o poma -g poma \"$${IBC_HOME}\" \"$${IBC_HOME}/logs\"" in script
    assert "install -m 600 -o poma -g poma \"$${IBC_DIR}/config.ini\" \"$${IBC_CONFIG}\"" in script
    assert "chmod 600 \"$${IBC_CONFIG}\"" in script
    assert "IBKR password" in script
    assert "GCP_BOOTSTRAP_SERVICE_ACCOUNT_KEY" not in script
    assert "TELEGRAM_BOT_TOKEN" not in script
