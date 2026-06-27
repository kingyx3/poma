from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
REPAIR_SCRIPT = REPO_ROOT / "ops/scripts/repair_ib_gateway_runtime.py"
INSTALL_HELPER = REPO_ROOT / "ops/scripts/install_ibc_config_helper.py"
OPS_WORKFLOW = REPO_ROOT / ".github/workflows/ib-gateway-ops.yml"
SERVICE_SCRIPT = REPO_ROOT / "ops/scripts/ensure_ibgateway_service.sh"


def test_repair_script_installs_current_runtime_components() -> None:
    script = REPAIR_SCRIPT.read_text(encoding="utf-8")

    for snippet in (
        "xvfb",
        "fluxbox",
        "x11vnc",
        "xterm",
        "openjdk-17-jre-headless",
        "netcat-openbsd",
        "APT_PACKAGES",
        "REQUIRED_COMMAND_PACKAGES",
    ):
        assert snippet in script


def test_repair_script_provisions_runtime_dirs_and_profiles() -> None:
    script = REPAIR_SCRIPT.read_text(encoding="utf-8")

    for snippet in (
        "/opt/ibgateway",
        "/opt/ibc",
        "/home/poma/Jts",
        "/home/poma/ibc/logs",
        "/run/poma-ibgateway",
        "/var/log/poma/ibgateway",
        "/tmp/poma-ibgateway",
        "chmod(mode)",
        "chown_recursive",
    ):
        assert snippet in script


def test_repair_script_installs_gateway_and_ibc_idempotently() -> None:
    script = REPAIR_SCRIPT.read_text(encoding="utf-8")

    for snippet in (
        "has_gateway_artifacts",
        "find_gateway_executable",
        "find_gateway_jars_dirs",
        "Installing IB Gateway into",
        "Installing IBC",
        "run_with_retry",
        "IB_GATEWAY_INSTALLER_URL",
        "IBC_ZIP_URL",
    ):
        assert snippet in script


def test_repair_script_bounds_network_installer_and_apt_work() -> None:
    script = REPAIR_SCRIPT.read_text(encoding="utf-8")

    for snippet in (
        "APT_TIMEOUT_SECONDS = 600",
        "DOWNLOAD_TIMEOUT_SECONDS = 600",
        "INSTALLER_TIMEOUT_SECONDS = 600",
        "NETWORK_RETRIES = 5",
        "timeout --kill-after=30s",
        "DPkg::Lock::Timeout=300",
        "stdin=subprocess.DEVNULL",
    ):
        assert snippet in script


def test_install_helper_owns_runner_and_systemd_unit() -> None:
    script = INSTALL_HELPER.read_text(encoding="utf-8")

    for snippet in (
        "HELPER_TARGET = Path(\"/usr/local/bin/poma-configure-ibc\")",
        "RUNNER_TARGET = Path(\"/usr/local/bin/poma-run-ib-gateway\")",
        "SERVICE_TARGET = Path(\"/etc/systemd/system/ibgateway.service\")",
        "RUNNER_TEXT",
        "SERVICE_TEXT",
        "ExecStart=/usr/local/bin/poma-run-ib-gateway",
        "Restart=always",
        "TimeoutStartSec=120",
        "MemoryMax=850M",
    ):
        assert snippet in script


def test_service_script_uses_installed_runner_and_unit() -> None:
    script = SERVICE_SCRIPT.read_text(encoding="utf-8")

    for snippet in (
        "/usr/local/bin/poma-run-ib-gateway",
        "/etc/systemd/system/ibgateway.service",
        "systemctl daemon-reload",
        "systemctl enable --now ibgateway",
    ):
        assert snippet in script

    assert "poma-ibgateway-headless" not in script


def test_install_helper_sets_expected_ibc_values() -> None:
    script = INSTALL_HELPER.read_text(encoding="utf-8")

    for snippet in (
        "set_ini IbLoginId",
        "set_ini IbPassword",
        "set_ini TradingMode",
        "set_ini ReloginAfterSecondFactorAuthenticationTimeout yes",
        "set_ini AcceptNonBrokerageAccountWarning yes",
        "set_ini ExistingSessionDetectedAction primaryoverride",
        "set_ini AutoRestartTime 23:45",
        "set_ini OverrideTwsApiPort 7497",
        "set_ini AcceptIncomingConnectionAction accept",
        "set_ini AllowBlindTrading yes",
    ):
        assert snippet in script


def test_install_helper_allows_missing_sample_config() -> None:
    script = INSTALL_HELPER.read_text(encoding="utf-8")

    assert "Missing IBC sample config" not in script
    assert "if [ -f \"${IBC_DIR}/config.ini\" ]; then" in script
    assert ": > \"${IBC_CONFIG}\"" in script
    assert "chmod 600 \"${IBC_CONFIG}\"" in script


def test_install_helper_pins_gateway_config_and_launcher_paths() -> None:
    script = INSTALL_HELPER.read_text(encoding="utf-8")

    for snippet in (
        "IBC_HOME=\"/home/poma/ibc\"",
        "IBC_CONFIG=\"${IBC_HOME}/config.ini\"",
        "TWS_SETTINGS_PATH=\"${TWS_SETTINGS_PATH:-/home/poma/Jts}\"",
        '"IBC_INI": "/home/poma/ibc/config.ini"',
        '"TWS_SETTINGS_PATH": "/home/poma/Jts"',
        "IB_GATEWAY_LAUNCH_DIR = Path(\"/opt/ibgateway-launch\")",
        "gateway_program_layout",
    ):
        assert snippet in script


def test_install_helper_writes_ib_login_id_not_generic_username_key() -> None:
    script = INSTALL_HELPER.read_text(encoding="utf-8")

    assert "set_ini IbLoginId \"${ib_login_id}\"" in script
    assert "set_ini TWSUSERID" not in script
    assert "set_ini UserName" not in script


def test_install_helper_pins_api_port_to_match_poma() -> None:
    script = INSTALL_HELPER.read_text(encoding="utf-8")

    # POMA (config.py IBKR_PORT) and the ops socket check both expect 7497; IB Gateway
    # otherwise defaults to 4002 (paper) / 4001 (live), so the port must be overridden.
    assert "set_ini OverrideTwsApiPort 7497" in script
    assert "set_ini AcceptIncomingConnectionAction accept" in script


def test_ops_workflow_surfaces_redacted_ibc_diagnostics() -> None:
    workflow = OPS_WORKFLOW.read_text(encoding="utf-8")

    assert "/home/poma/ibc/logs/*.txt" in workflow
    assert "sed -E" in workflow
    assert "=***" in workflow
    assert "IbLoginId" in workflow
    assert "TWSUSERID" in workflow


def test_ops_workflow_waits_for_gateway_socket_readiness() -> None:
    workflow = OPS_WORKFLOW.read_text(encoding="utf-8")

    assert "IB_GATEWAY_2FA_APPROVAL_TIMEOUT_SECONDS: 300" in workflow
    assert "IB_GATEWAY_SOCKET_POLL_SECONDS: 5" in workflow
    assert "Waiting up to ${timeout_seconds}s (5 minutes) for IBKR 2FA approval" in workflow
    assert "while [ \"${SECONDS}\" -lt \"${deadline}\" ]; do" in workflow
    assert "if timed \"Socket/service poll attempt ${attempt}\" poll_gateway_socket_once; then" in workflow
    assert "systemctl is-active --quiet ibgateway" in workflow
    assert "Waiting for IBKR 2FA approval / Gateway API socket" in workflow
    assert "IBKR 2FA approval or Gateway API readiness timed out" in workflow
