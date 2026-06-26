from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
REFRESH_SCRIPT = REPO_ROOT / "ops/scripts/refresh_gateway_helper.sh"
SERVICE_SCRIPT = REPO_ROOT / "ops/scripts/ensure_ibgateway_service.sh"


def test_refresh_script_uploads_service_repair_helper() -> None:
    script = REFRESH_SCRIPT.read_text(encoding="utf-8")

    assert "ensure_ibgateway_service.sh" in script
    assert "install_ibc_config_helper.py" in script


def test_service_repair_helper_defines_gateway_unit() -> None:
    script = SERVICE_SCRIPT.read_text(encoding="utf-8")

    assert script.startswith("#!/bin/sh\n")
    assert "set -euo pipefail" not in script.split("cat >/usr/local/bin/poma-run-ib-gateway", 1)[0]
    assert "ibgateway.service" in script
    assert "poma-run-ib-gateway" in script
    assert "daemon-reload" in script
