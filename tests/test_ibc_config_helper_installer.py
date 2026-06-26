import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
HELPER_PATH = REPO_ROOT / "ops/scripts/install_ibc_config_helper.py"


def load_patch_helper_text():
    spec = importlib.util.spec_from_file_location("install_ibc_config_helper", HELPER_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.patch_helper_text


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
