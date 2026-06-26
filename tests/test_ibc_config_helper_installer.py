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


def load_patch_helper_text():
    return load_helper_module().patch_helper_text


def test_patch_helper_text_allows_missing_template() -> None:
    original = """
if [ ! -f "${IBC_DIR}/config.ini" ]; then
  echo "Missing IBC sample config at ${IBC_DIR}/config.ini" >&2
  exit 1
fi

if [ ! -f "${IBC_CONFIG}" ]; then
  install -m 600 -o poma -g poma "${IBC_DIR}/config.ini" "${IBC_CONFIG}"
fi
""".lstrip()

    rendered = load_patch_helper_text()(original)

    assert "Missing IBC sample config" not in rendered
    assert 'if [ -f "${IBC_DIR}/config.ini" ]; then' in rendered
    assert ': > "${IBC_CONFIG}"' in rendered


def test_installer_repairs_gateway_runner_and_service() -> None:
    module = load_helper_module()

    assert "poma-run-ib-gateway" in module.RUNNER_TARGET.as_posix()
    assert "ibgateway.service" in module.SERVICE_TARGET.as_posix()
    assert "require_command Xvfb" in module.RUNNER_TEXT
    assert "find \"${IB_GATEWAY_DIR}\" -type f -name ibgateway" in module.RUNNER_TEXT
    assert "ExecStart=/usr/local/bin/poma-run-ib-gateway" in module.SERVICE_TEXT


def test_installer_reconfigures_ibc_launcher() -> None:
    script = HELPER_PATH.read_text(encoding="utf-8")

    assert "def configure_ibc_launcher" in script
    assert "TWS_MAJOR_VRSN" in script
    assert "TWS_PATH" in script
    assert "JAVA_PATH" in script
