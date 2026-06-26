from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SERVICE_SCRIPT = REPO_ROOT / "ops/scripts/ensure_ibgateway_service.sh"
INSTALL_HELPER = REPO_ROOT / "ops/scripts/install_ibc_config_helper.py"
REPAIR_HELPER = REPO_ROOT / "ops/scripts/repair_ib_gateway_runtime.py"
OPS_WORKFLOW = REPO_ROOT / ".github/workflows/ib-gateway-ops.yml"
RUNNER_MARKER = "cat >/usr/local/bin/poma-run-ib-gateway"


def test_service_repair_helper_defines_gateway_unit() -> None:
    script = SERVICE_SCRIPT.read_text(encoding="utf-8")

    assert script.startswith("#!/bin/sh\n")
    assert "set -euo pipefail" not in script.split(RUNNER_MARKER, 1)[0]
    assert "ibgateway.service" in script
    assert "poma-run-ib-gateway" in script
    assert "daemon-reload" in script


def test_service_repair_runner_fails_clearly_for_missing_gateway_binary() -> None:
    script = SERVICE_SCRIPT.read_text(encoding="utf-8")

    assert "find \"${IB_GATEWAY_DIR}\" -type f -name ibgateway" in script
    assert "Unable to find an executable IB Gateway binary" in script
    assert "Run IB Gateway Ops to repair the VM bootstrap and install IB Gateway" in script
    assert "exit 127" in script


def test_service_repair_runner_checks_desktop_dependencies() -> None:
    script = SERVICE_SCRIPT.read_text(encoding="utf-8")

    assert "require_command Xvfb" in script
    assert "require_command fluxbox" in script
    assert "require_command x11vnc" in script


def test_service_repair_uses_systemd_managed_runtime_logs() -> None:
    script = SERVICE_SCRIPT.read_text(encoding="utf-8")

    assert "RuntimeDirectory=poma-ibgateway" in script
    assert "LogsDirectory=poma/ibgateway" in script
    assert "IB_GATEWAY_LOG_DIR=/var/log/poma/ibgateway" in script
    assert '>"${IB_GATEWAY_LOG_DIR}/xvfb.log"' in script


def test_runtime_repair_helper_is_self_contained() -> None:
    script = INSTALL_HELPER.read_text(encoding="utf-8")

    assert "CONFIG_HELPER_TEXT" in script
    assert "REQUIRED_COMMAND_PACKAGES" in script
    assert "apt-get" in script
    assert "SOURCE = Path" not in script
    assert "extract_config_helper" not in script


def test_runtime_repair_helper_uses_systemd_runtime_logs() -> None:
    script = INSTALL_HELPER.read_text(encoding="utf-8")

    assert "RuntimeDirectory=poma-ibgateway" in script
    assert "LogsDirectory=poma/ibgateway" in script
    assert "IB_GATEWAY_LOG_DIR" in script
    assert "/tmp/poma-ibgateway/xvfb.log" not in script
    assert "/tmp/poma-ibgateway/fluxbox.log" not in script
    assert "/tmp/poma-ibgateway/x11vnc.log" not in script


def test_runtime_repair_installs_missing_gateway_artifacts() -> None:
    script = REPAIR_HELPER.read_text(encoding="utf-8")

    assert "IB_GATEWAY_INSTALLER_URL" in script
    assert "IBC_ZIP_URL" in script
    assert "def ensure_ib_gateway_installed" in script
    assert "def ensure_ibc_installed" in script
    assert "LEGACY_RUNTIME_DIR" in script
    assert "IB_GATEWAY_LOG_DIR" in script


def test_runtime_repair_accepts_gateway_jars_as_installed_artifacts() -> None:
    script = REPAIR_HELPER.read_text(encoding="utf-8")

    assert "def find_gateway_jars_dirs" in script
    assert "def has_gateway_artifacts" in script
    assert "find_gateway_executable() is not None or bool(find_gateway_jars_dirs())" in script
    assert "no executable or jars were found" in script


def test_ibc_launcher_uses_numeric_gateway_version() -> None:
    script = INSTALL_HELPER.read_text(encoding="utf-8")

    assert "def find_numeric_ancestor" in script
    assert "def gateway_version_from_jars_dir" in script
    assert "ancestor.name.isdigit()" in script
    assert "gateway_major_version = gateway_version_from_jars_dir(gateway_jars_dir, text)" in script
    assert "gateway_major_version = gateway_jars_dir.parent.name" not in script


def test_ibc_launcher_handles_flat_gateway_install_layout() -> None:
    script = INSTALL_HELPER.read_text(encoding="utf-8")

    assert 'DEFAULT_IB_GATEWAY_MAJOR_VERSION = "1019"' in script
    assert "def current_gatewaystart_version" in script
    assert "existing_version = current_gatewaystart_version(gatewaystart_text)" in script
    assert "return IB_GATEWAY_DIR" in script
    assert "Unable to determine numeric IB Gateway version" not in script


def test_ops_workflow_surfaces_redacted_ibc_diagnostics() -> None:
    workflow = OPS_WORKFLOW.read_text(encoding="utf-8")

    assert "/home/poma/ibc/logs/*.txt" in workflow
    assert "IbPassword" in workflow
    assert "TWSPASSWORD" in workflow
    assert "IbLoginId" in workflow
    assert "TWSUSERID" in workflow
