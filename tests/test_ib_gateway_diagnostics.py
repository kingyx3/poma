import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DIAGNOSTICS_PATH = REPO_ROOT / "ops/scripts/diagnose_ib_gateway_runtime.py"


def load_diagnostics_module():
    spec = importlib.util.spec_from_file_location("diagnose_ib_gateway_runtime", DIAGNOSTICS_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def classify(**overrides):
    module = load_diagnostics_module()
    state = {
        "service_exists": True,
        "service_active": True,
        "api_socket_open": False,
        "config_exists": True,
        "has_xvfb": True,
        "has_fluxbox": True,
        "has_x11vnc": True,
        "has_gatewaystart": True,
        "has_java": True,
        "has_ibgateway": True,
        "log_text": "",
    }
    state.update(overrides)
    return module.classify_startup_state(**state)


def test_classifies_service_active_without_headless_display_as_prelogin_failure() -> None:
    result = classify(has_xvfb=False)

    assert result.stage == "service-active-no-xvfb"
    assert result.action == "fail"
    assert "Xvfb" in result.reason


def test_classifies_configured_service_without_ibc_as_prelogin_failure() -> None:
    result = classify(has_gatewaystart=False)

    assert result.stage == "ibc-not-running"
    assert result.action == "fail"
    assert "Gateway likely never reached login" in result.reason


def test_classifies_no_java_gateway_process_as_prelogin_failure() -> None:
    result = classify(has_java=False, has_ibgateway=False)

    assert result.stage == "java-gateway-not-running"
    assert result.action == "fail"
    assert "no IBKR mobile notification" in result.reason


def test_classifies_2fa_log_progress_as_wait_for_approval() -> None:
    result = classify(log_text="Waiting for second factor authentication approval")

    assert result.stage == "login-reached-2fa-pending"
    assert result.action == "continue"


def test_classifies_open_api_socket_as_ready_for_real_handshake() -> None:
    result = classify(api_socket_open=True, has_xvfb=False, has_java=False)

    assert result.stage == "api-socket-open"
    assert result.action == "ready"
