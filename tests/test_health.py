from __future__ import annotations

from conftest import make_settings

from poma.broker import IbkrHealth
from poma.health import check_data_provider, check_ibkr, run_checks


def test_check_data_provider_fixture_ok() -> None:
    check = check_data_provider(make_settings())
    assert check.ok
    assert "fixture" in check.detail


def test_check_ibkr_skipped_in_dry_run() -> None:
    check = check_ibkr(make_settings(TRADING_MODE="dry_run"))
    assert check.ok
    assert "skipped" in check.detail


def test_check_ibkr_reports_connection_failure(monkeypatch) -> None:
    def boom(settings, *, timeout: float = 20.0) -> IbkrHealth:
        raise ConnectionError("no socket")

    monkeypatch.setattr("poma.broker.probe_ibkr", boom)
    check = check_ibkr(make_settings(TRADING_MODE="paper", IBKR_ACCOUNT="DU123"))
    assert not check.ok
    assert "unreachable" in check.detail


def test_check_ibkr_success_when_account_matches_and_session_is_trade_enabled(monkeypatch) -> None:
    monkeypatch.setattr(
        "poma.broker.probe_ibkr",
        lambda settings, *, timeout=20.0: IbkrHealth(
            True,
            ["DU123"],
            "now",
            3,
            True,
            "what-if order preview accepted for AAPL",
            True,
            "received live tick for AAPL",
        ),
    )
    check = check_ibkr(make_settings(TRADING_MODE="paper", IBKR_ACCOUNT="DU123"))
    assert check.ok
    assert "DU123" in check.detail
    assert "what-if order preview accepted" in check.detail


def test_check_ibkr_fails_when_session_is_not_trade_enabled(monkeypatch) -> None:
    monkeypatch.setattr(
        "poma.broker.probe_ibkr",
        lambda settings, *, timeout=20.0: IbkrHealth(
            True,
            ["DU123"],
            "now",
            3,
            False,
            "IBKR session is connected but not trading-enabled",
            True,
            "received live tick for AAPL",
        ),
    )
    check = check_ibkr(make_settings(TRADING_MODE="paper", IBKR_ACCOUNT="DU123"))
    assert not check.ok
    assert "not trading-enabled" in check.detail


def test_check_ibkr_fails_on_account_mismatch(monkeypatch) -> None:
    monkeypatch.setattr(
        "poma.broker.probe_ibkr",
        lambda settings, *, timeout=20.0: IbkrHealth(
            True,
            ["DUOTHER"],
            "now",
            0,
            True,
            "what-if order preview accepted for AAPL",
            True,
            "received live tick for AAPL",
        ),
    )
    check = check_ibkr(make_settings(TRADING_MODE="paper", IBKR_ACCOUNT="DU123"))
    assert not check.ok
    assert "not in" in check.detail


def test_check_ibkr_fails_when_market_data_never_arrives(monkeypatch) -> None:
    monkeypatch.setattr(
        "poma.broker.probe_ibkr",
        lambda settings, *, timeout=20.0: IbkrHealth(
            True,
            ["DU123"],
            "now",
            3,
            True,
            "what-if order preview accepted for AAPL",
            False,
            "no market data tick received for AAPL after 3s; ibkr said: 354: Requested market data is not subscribed.",
        ),
    )
    check = check_ibkr(make_settings(TRADING_MODE="paper", IBKR_ACCOUNT="DU123"))
    assert not check.ok
    assert "354: Requested market data is not subscribed" in check.detail


def test_run_checks_covers_runtime_config_provider_and_ibkr() -> None:
    names = {check.name for check in run_checks(make_settings(TRADING_MODE="dry_run"))}
    assert names == {"runtime_config", "data_provider", "ibkr"}
