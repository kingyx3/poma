import contextlib
import importlib.util
import io
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


def _run_validate_owner_check(tmp_path, monkeypatch, *, file_uid, poma_uid, owner_name):
    """Run validate_config against a temp config and return the owner-related error, if any."""
    module = load_diagnostics_module()
    config = tmp_path / "config.ini"
    config.write_text(
        "IbLoginId=abc\nIbPassword=secret\nTradingMode=paper\nOverrideTwsApiPort=7497\n"
        "AcceptIncomingConnectionAction=accept\nAllowBlindTrading=yes\n"
        "ReloginAfterSecondFactorAuthenticationTimeout=yes\n",
        encoding="utf-8",
    )
    config.chmod(0o600)
    monkeypatch.setattr(module, "IBC_CONFIG", config)

    real_stat = type(config).stat

    def fake_stat(self, *args, **kwargs):
        result = real_stat(self, *args, **kwargs)
        if self == config:
            return type("S", (), {"st_uid": file_uid, "st_mode": result.st_mode})()
        return result

    monkeypatch.setattr(type(config), "stat", fake_stat)
    monkeypatch.setattr(module.pwd, "getpwuid", lambda uid: type("P", (), {"pw_name": owner_name})())
    monkeypatch.setattr(module.pwd, "getpwnam", lambda name: type("P", (), {"pw_uid": poma_uid})())

    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        module.validate_config("paper")
    return [line for line in buffer.getvalue().splitlines() if "owner" in line and "ERROR" in line]


def test_validate_accepts_config_owned_by_poma_uid_shared_with_ubuntu(tmp_path, monkeypatch) -> None:
    # startup.sh creates poma as a non-unique uid 1000 shared with the cloud image's ubuntu
    # user, so the file's uid resolves to name "ubuntu". Ownership must validate by uid, not
    # name, or configure-paper fails with a spurious "owner is ubuntu, expected poma".
    owner_errors = _run_validate_owner_check(
        tmp_path, monkeypatch, file_uid=1000, poma_uid=1000, owner_name="ubuntu"
    )
    assert owner_errors == []


def test_validate_rejects_config_owned_by_a_different_uid(tmp_path, monkeypatch) -> None:
    owner_errors = _run_validate_owner_check(
        tmp_path, monkeypatch, file_uid=0, poma_uid=1000, owner_name="root"
    )
    assert owner_errors and "expected 1000" in owner_errors[0]


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


def test_2fa_hint_takes_priority_over_fatal_log_hint() -> None:
    # FATAL_LOG_HINTS is too broad and matches normal IBC startup output.
    # When logs contain both noisy fatal-hint words and a 2FA keyword, the
    # 2FA stage must win so the operator knows to approve the mobile prompt.
    result = classify(
        log_text="failed to connect; invalid session. Waiting for second factor authentication."
    )

    assert result.stage == "login-reached-2fa-pending"
    assert result.action == "continue"


def test_gateway_log_error_is_never_fail_fast() -> None:
    # FATAL_LOG_HINTS matches too many routine startup log lines; gateway-log-error
    # must use "continue" so the poll loop keeps running until 2FA/login is detected.
    result = classify(log_text="failed to connect; invalid certificate")

    assert result.stage == "gateway-log-error"
    assert result.action == "continue"


def test_classifies_open_api_socket_as_ready_for_real_handshake() -> None:
    result = classify(api_socket_open=True, has_xvfb=False, has_java=False)

    assert result.stage == "api-socket-open"
    assert result.action == "ready"


def test_recent_log_hints_prints_only_actionable_lines(tmp_path, monkeypatch) -> None:
    # A "hints" section that dumps full tails (VNC banners, screen-setup chatter) buries the one
    # actionable line; only login/2FA/error-shaped lines may survive the filter.
    module = load_diagnostics_module()
    log_dir = tmp_path / "ibgateway"
    log_dir.mkdir()
    (log_dir / "xvfb.log").write_text(
        "\n".join(
            [
                "Have you tried the x11vnc '-ncache' VNC client-side pixel caching feature yet?",
                "The VNC desktop is:      localhost:0",
                "screen setup finished.",
                "API connection failed: TimeoutError()",
                "Waiting for second factor authentication.",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(module, "LOG_PATHS", (log_dir,))

    hints = module.recent_log_hints(200)

    assert "API connection failed: TimeoutError()" in hints
    assert "second factor authentication" in hints
    assert "hint lines only" in hints
    assert "ncache" not in hints
    assert "screen setup finished" not in hints


def test_recent_log_hints_caps_lines_per_file(tmp_path, monkeypatch) -> None:
    module = load_diagnostics_module()
    log_dir = tmp_path / "ibgateway"
    log_dir.mkdir()
    (log_dir / "ibc.log").write_text(
        "\n".join(f"error line {index}" for index in range(100)),
        encoding="utf-8",
    )
    monkeypatch.setattr(module, "LOG_PATHS", (log_dir,))

    hints = module.recent_log_hints(200)

    assert "error line 99" in hints
    assert "error line 10" not in hints
    assert "60 earlier hint line(s) omitted" in hints
