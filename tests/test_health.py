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


def test_check_ibkr_success_when_account_matches(monkeypatch) -> None:
    monkeypatch.setattr(
        "poma.broker.probe_ibkr",
        lambda settings, *, timeout=20.0: IbkrHealth(True, ["DU123"], "now", 3),
    )
    check = check_ibkr(make_settings(TRADING_MODE="paper", IBKR_ACCOUNT="DU123"))
    assert check.ok
    assert "DU123" in check.detail


def test_check_ibkr_fails_on_account_mismatch(monkeypatch) -> None:
    monkeypatch.setattr(
        "poma.broker.probe_ibkr",
        lambda settings, *, timeout=20.0: IbkrHealth(True, ["DUOTHER"], "now", 0),
    )
    check = check_ibkr(make_settings(TRADING_MODE="paper", IBKR_ACCOUNT="DU123"))
    assert not check.ok
    assert "not in" in check.detail


def test_run_checks_covers_provider_and_ibkr() -> None:
    names = {check.name for check in run_checks(make_settings(TRADING_MODE="dry_run"))}
    assert names == {"data_provider", "ibkr"}
