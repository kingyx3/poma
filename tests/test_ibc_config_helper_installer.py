from ops.scripts.install_ibc_config_helper import patch_helper_text


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

    rendered = patch_helper_text(original)

    assert "Missing IBC sample config" not in rendered
    assert 'if [ -f "${IBC_DIR}/config.ini" ]; then' in rendered
    assert ': > "${IBC_CONFIG}"' in rendered
