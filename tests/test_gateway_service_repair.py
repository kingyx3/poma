import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SERVICE_SCRIPT = REPO_ROOT / "ops/scripts/ensure_ibgateway_service.sh"
INSTALL_HELPER = REPO_ROOT / "ops/scripts/install_ibc_config_helper.py"
REPAIR_HELPER = REPO_ROOT / "ops/scripts/repair_ib_gateway_runtime.py"
OPS_WORKFLOW = REPO_ROOT / ".github/workflows/ib-gateway-ops.yml"


def test_service_shim_only_ensures_running_with_single_source_of_truth() -> None:
    script = SERVICE_SCRIPT.read_text(encoding="utf-8")

    assert script.startswith("#!/bin/sh\n")
    # The shim must not redefine the runner or unit; that lives in the Python installer.
    assert "cat >/usr/local/bin/poma-run-ib-gateway" not in script
    assert "cat >/etc/systemd/system/ibgateway.service" not in script
    assert "install_ibc_config_helper.py" in script
    assert "systemctl daemon-reload" in script
    assert "systemctl enable --now ibgateway" in script


def test_service_shim_fails_fast_when_runner_or_unit_missing() -> None:
    script = SERVICE_SCRIPT.read_text(encoding="utf-8")

    assert "/usr/local/bin/poma-run-ib-gateway" in script
    assert "/etc/systemd/system/ibgateway.service" in script
    assert "exit 1" in script


def test_installer_runner_fails_clearly_for_missing_gateway_binary() -> None:
    script = INSTALL_HELPER.read_text(encoding="utf-8")

    assert 'find "${IB_GATEWAY_DIR}" -type f -name ibgateway' in script
    assert "Unable to find an executable IB Gateway binary" in script
    assert "Run IB Gateway Ops to repair the VM bootstrap and install IB Gateway" in script
    assert "exit 127" in script


def test_installer_runner_checks_desktop_dependencies() -> None:
    script = INSTALL_HELPER.read_text(encoding="utf-8")

    assert "require_command Xvfb" in script
    assert "require_command fluxbox" in script
    assert "require_command x11vnc" in script


def test_installer_uses_systemd_managed_runtime_logs() -> None:
    script = INSTALL_HELPER.read_text(encoding="utf-8")

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


def _load_install_helper():
    spec = importlib.util.spec_from_file_location("install_ibc_config_helper", INSTALL_HELPER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _make_jars(program_dir: Path) -> Path:
    jars = program_dir / "jars"
    jars.mkdir(parents=True)
    (jars / "ibgateway.jar").write_text("")
    (program_dir / ".install4j").mkdir()
    (program_dir / "ibgateway.vmoptions").write_text("")
    return jars


def test_ibc_launcher_uses_numeric_gateway_version() -> None:
    script = INSTALL_HELPER.read_text(encoding="utf-8")

    assert "def find_numeric_ancestor" in script
    assert "def gateway_version_from_jars_dir" in script
    assert "ancestor.name.isdigit()" in script
    assert "gateway_program_layout(gateway_jars_dir, text)" in script
    assert "gateway_major_version = gateway_jars_dir.parent.name" not in script


def test_ibc_launcher_handles_flat_gateway_install_layout(tmp_path) -> None:
    script = INSTALL_HELPER.read_text(encoding="utf-8")

    assert 'DEFAULT_IB_GATEWAY_MAJOR_VERSION = "1019"' in script
    assert "def current_gatewaystart_version" in script
    assert "existing_version = current_gatewaystart_version(gatewaystart_text)" in script
    assert "Unable to determine numeric IB Gateway version" not in script

    module = _load_install_helper()
    install_dir = tmp_path / "opt" / "ibgateway"
    jars = _make_jars(install_dir)  # flat: jars live directly under the install dir
    module.IB_GATEWAY_DIR = install_dir
    module.IB_GATEWAY_LAUNCH_DIR = tmp_path / "opt" / "ibgateway-launch"

    tws_path, version = module.gateway_program_layout(jars, "TWS_MAJOR_VRSN=1019\n")

    # IBC resolves the gateway jars as ${TWS_PATH}/ibgateway/${version}/jars and only keeps
    # the ibgateway.vmoptions source when that *primary* path holds the jars folder.
    gateway_program = Path(tws_path) / "ibgateway" / version
    assert (gateway_program / "jars").is_dir()
    assert (gateway_program / "ibgateway.vmoptions").is_file()


def test_ibc_launcher_uses_versioned_layout_directly(tmp_path) -> None:
    module = _load_install_helper()
    install_dir = tmp_path / "opt" / "ibgateway"
    program = install_dir / "ibgateway" / "1019"
    jars = _make_jars(program)
    module.IB_GATEWAY_DIR = install_dir
    module.IB_GATEWAY_LAUNCH_DIR = tmp_path / "launch"

    tws_path, version = module.gateway_program_layout(jars, "TWS_MAJOR_VRSN=1019\n")

    assert version == "1019"
    assert Path(tws_path) == install_dir  # already structured; no symlink farm created
    assert not (tmp_path / "launch").exists()
    assert (Path(tws_path) / "ibgateway" / version / "jars").is_dir()


def test_ibc_config_helper_pins_api_port_to_match_poma() -> None:
    script = INSTALL_HELPER.read_text(encoding="utf-8")

    # POMA (config.py IBKR_PORT) and the ops socket check both expect 7497; IB Gateway
    # otherwise defaults to 4002 (paper) / 4001 (live), so the port must be overridden.
    assert "set_ini OverrideTwsApiPort 7497" in script
    assert "set_ini AcceptIncomingConnectionAction accept" in script


def test_ops_workflow_surfaces_redacted_ibc_diagnostics() -> None:
    workflow = OPS_WORKFLOW.read_text(encoding="utf-8")

    assert "/home/poma/ibc/logs/*.txt" in workflow
    assert "IbPassword" in workflow
    assert "TWSPASSWORD" in workflow
    assert "IbLoginId" in workflow
    assert "TWSUSERID" in workflow


def test_ops_workflow_waits_for_gateway_socket_readiness() -> None:
    workflow = OPS_WORKFLOW.read_text(encoding="utf-8")

    assert "IB_GATEWAY_SOCKET_TIMEOUT_SECONDS: 300" in workflow
    assert "IB_GATEWAY_SOCKET_POLL_SECONDS: 5" in workflow
    assert "after the 2FA prompt" in workflow
    assert "while [ \"${SECONDS}\" -lt \"${deadline}\" ]; do" in workflow
    assert "systemctl is-active --quiet ibgateway" in workflow
    assert "Waiting for IB Gateway API socket... ${elapsed}/${timeout_seconds}s" in workflow
    assert "within ${timeout_seconds}s after the 2FA prompt" in workflow
