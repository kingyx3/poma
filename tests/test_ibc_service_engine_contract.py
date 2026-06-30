from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SERVICE_SCRIPT = REPO_ROOT / "ops/scripts/ensure_ibgateway_service.sh"


def test_service_script_renders_ibc_managed_runner_and_engine() -> None:
    script = SERVICE_SCRIPT.read_text(encoding="utf-8")

    for snippet in (
        "cat >\"${RUNNER}\"",
        "cat >\"${ENGINE}\"",
        "exec /usr/local/bin/poma-ibc-gateway-engine",
        "Starting IBC gatewaystart.sh -inline",
        "refusing raw Gateway fallback",
        "systemctl enable --now ibgateway",
    ):
        assert snippet in script


def test_service_script_waits_for_real_gateway_process_not_wrapper_path() -> None:
    script = SERVICE_SCRIPT.read_text(encoding="utf-8")

    assert "ibcalpha\\.ibc\\.IbcGateway|/ibgateway" in script
    assert "pgrep -u \"$(id -u)\" -f 'java|ibgateway'" not in script
    assert "Real Gateway process or API listener detected" in script


def test_service_script_extends_ibc_login_dialog_timeout() -> None:
    script = SERVICE_SCRIPT.read_text(encoding="utf-8")

    assert "IBC_LOGIN_DIALOG_DISPLAY_TIMEOUT_SECONDS:-240" in script
    assert "set_ini LoginDialogDisplayTimeout" in script
    assert "Pinned IBC LoginDialogDisplayTimeout" in script


def test_service_script_does_not_fragile_patch_generated_files() -> None:
    script = SERVICE_SCRIPT.read_text(encoding="utf-8")

    assert "runner_text = RUNNER.read_text" not in script
    assert "unit_text = UNIT.read_text" not in script
    assert "ENGINE.write_text" not in script
