from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
STARTUP_SCRIPT = REPO_ROOT / "infra/gcp-free-tier/startup.sh"


REQUIRED_STARTUP_SNIPPETS = (
    "cat >/etc/systemd/system/ibgateway.service",
    "ExecStart=/usr/local/bin/poma-run-ib-gateway",
    'exec "$${IBC_DIR}/gatewaystart.sh" -inline',
    'exec "$${IB_GATEWAY_DIR}/ibgateway"',
    "Restart=always",
    'IBC_INI": "/home/poma/ibc/config.ini"',
    'TWS_PATH": tws_path',
    'TWS_SETTINGS_PATH": "/home/poma/Jts"',
    'LOG_PATH": "/home/poma/ibc/logs"',
    (
        "find \"$${IB_GATEWAY_DIR}\" -type d "
        "-path '*/ibgateway/[0-9]*/jars'"
    ),
    (
        'install -d -m 700 -o poma -g poma '
        '"$${IBC_HOME}" "$${IBC_HOME}/logs"'
    ),
    (
        'install -m 600 -o poma -g poma '
        '"$${IBC_DIR}/config.ini" "$${IBC_CONFIG}"'
    ),
    'chmod 600 "$${IBC_CONFIG}"',
)


def test_gcp_startup_script_keeps_gateway_runtime_contracts() -> None:
    script = STARTUP_SCRIPT.read_text(encoding="utf-8")

    for snippet in REQUIRED_STARTUP_SNIPPETS:
        assert snippet in script
