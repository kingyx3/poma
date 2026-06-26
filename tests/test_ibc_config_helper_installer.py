import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
HELPER_PATH = REPO_ROOT / "ops/scripts/install_ibc_config_helper.py"


def load_helper_module():
    spec = importlib.util.spec_from_file_location("install_ibc_config_helper", HELPER_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_config_helper_text_allows_missing_template() -> None:
    module = load_helper_module()

    assert "Missing IBC sample config" not in module.CONFIG_HELPER_TEXT
    assert 'if [ -f "${IBC_DIR}/config.ini" ]; then' in module.CONFIG_HELPER_TEXT
    assert ': > "${IBC_CONFIG}"' in module.CONFIG_HELPER_TEXT


def test_installer_repairs_gateway_runner_and_service() -> None:
    module = load_helper_module()

    assert "poma-run-ib-gateway" in module.RUNNER_TARGET.as_posix()
    assert "ibgateway.service" in module.SERVICE_TARGET.as_posix()
    assert "require_command Xvfb" in module.RUNNER_TEXT
    assert 'find "${IB_GATEWAY_DIR}" -type f -name ibgateway' in module.RUNNER_TEXT
    assert "ExecStart=/usr/local/bin/poma-run-ib-gateway" in module.SERVICE_TEXT


def test_installer_reconfigures_ibc_launcher() -> None:
    script = HELPER_PATH.read_text(encoding="utf-8")

    assert "def configure_ibc_launcher" in script
    assert "TWS_MAJOR_VRSN" in script
    assert "TWS_PATH" in script
    assert "JAVA_PATH" in script
