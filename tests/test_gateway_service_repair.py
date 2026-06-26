from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SERVICE_SCRIPT = REPO_ROOT / "ops/scripts/ensure_ibgateway_service.sh"
INSTALL_HELPER = REPO_ROOT / "ops/scripts/install_ibc_config_helper.py"
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
    assert "exit 127" in script


def test_service_repair_runner_checks_desktop_dependencies() -> None:
    script = SERVICE_SCRIPT.read_text(encoding="utf-8")

    assert "require_command Xvfb" in script
    assert "require_command fluxbox" in script
    assert "require_command x11vnc" in script


def test_runtime_repair_helper_is_self_contained() -> None:
    script = INSTALL_HELPER.read_text(encoding="utf-8")

    assert "CONFIG_HELPER_TEXT" in script
    assert "REQUIRED_COMMAND_PACKAGES" in script
    assert "apt-get" in script
    assert "SOURCE = Path" not in script
    assert "extract_config_helper" not in script
