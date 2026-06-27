from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
REPAIR_SCRIPT = REPO_ROOT / "ops/scripts/repair_ib_gateway_runtime.py"
INSTALL_HELPER = REPO_ROOT / "ops/scripts/install_ibc_config_helper.py"
OPS_WORKFLOW = REPO_ROOT / ".github/workflows/ib-gateway-ops.yml"
SERVICE_SCRIPT = REPO_ROOT / "ops/scripts/ensure_ibgateway_service.sh"


def test_repair_script_installs_headless_runtime_components() -> None:
    script = REPAIR_SCRIPT.read_text(encoding="utf-8")

    assert "xvfb" in script
    assert "libxrender1" in script
    assert "libxtst6" in script
    assert "libxi6" in script
    assert "libxrandr2" in script
    assert "openbox" in script
    assert "pyautogui" in script
    assert "POMA_IBG_RUNTIME_PACKAGES" in script


def test_repair_script_provisions_runtime_dirs_and_profiles() -> None:
    script = REPAIR_SCRIPT.read_text(encoding="utf-8")

    assert "/opt/ibgateway" in script
    assert "/home/poma/ibc" in script
    assert "/var/log/poma/ibgateway" in script
    assert "IBC_INI" in script
    assert "config.ini" in script
    assert "jts.ini" in script
    assert "chmod(0o700)" in script


def test_repair_script_writes_headless_launch_wrapper() -> None:
    script = REPAIR_SCRIPT.read_text(encoding="utf-8")

    assert "poma-ibgateway-headless" in script
    assert "exec xvfb-run" in script
    assert "openbox" in script
    assert "ibcstart.sh" in script
    assert "DISPLAY" in script


def test_service_script_uses_headless_wrapper() -> None:
    script = SERVICE_SCRIPT.read_text(encoding="utf-8")

    assert "ExecStart=/usr/local/bin/poma-ibgateway-headless" in script
    assert "RuntimeMaxSec=43200" in script
    assert "Restart=on-failure" in script
    assert "systemctl enable ibgateway.service" in script


def test_repair_script_keeps_gateway_installer_idempotent() -> None:
    script = REPAIR_SCRIPT.read_text(encoding="utf-8")

    assert "IB Gateway already installed" in script
    assert "needs_gateway_install" in script
    assert "download_with_retries" in script
    assert "POMA_IBG_DOWNLOAD_URL" in script
    assert "POMA_IBG_INSTALLER" in script


def test_repair_script_can_patch_ibc_login_safely() -> None:
    script = REPAIR_SCRIPT.read_text(encoding="utf-8")

    assert "patch_ibc_login_script" in script
    assert "existing_exports" in script
    assert "Preserve any local custom exports" in script
    assert "IBC_INI" in script
    assert "TWS_SETTINGS_PATH" in script


def test_install_helper_sets_expected_ibc_values() -> None:
    script = INSTALL_HELPER.read_text(encoding="utf-8")

    assert "set_ini TradingMode" in script
    assert "set_ini IbLoginId" in script
    assert "set_ini StoreSettingsOnServer no" in script
    assert "set_ini ReadOnlyApi no" in script
    assert "poma-configure-ibc" in script
    assert "shred -u" in script


def test_install_helper_allows_missing_sample_config() -> None:
    script = INSTALL_HELPER.read_text(encoding="utf-8")

    assert "Missing IBC sample config" not in script
    assert "Sample IBC config not found" in script
    assert "touch \"${IBC_CONFIG}\"" in script


def test_install_helper_pins_gateway_config_path() -> None:
    script = INSTALL_HELPER.read_text(encoding="utf-8")

    assert "IBC_CONFIG=\"/home/poma/ibc/config.ini\"" in script
    assert "IBC_INI_DIR=\"/home/poma/Jts\"" in script
    assert "IBC_INI=\"${IBC_INI_DIR}/jts.ini\"" in script
    assert "POMA_LAUNCHER=\"/usr/local/bin/poma-ibgateway-headless\"" in script


def test_install_helper_writes_username_not_generic_key() -> None:
    script = INSTALL_HELPER.read_text(encoding="utf-8")

    assert "set_ini IbLoginId \"${ib_user}\"" in script
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
    assert "while [" in workflow and "${SECONDS}" in workflow and "${deadline}" in workflow
    assert "systemctl is-active --quiet ibgateway" in workflow
    assert "Waiting for IBKR 2FA approval / Gateway API socket" in workflow
    assert "IBKR 2FA approval or Gateway API readiness timed out" in workflow
