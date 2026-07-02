from __future__ import annotations

from datetime import UTC, datetime

import pytest
from conftest import make_settings

from poma.cli import _assert_rebalance_market_window
from poma.market_calendar import MarketDecision, is_market_open


def test_paper_rebalance_requires_open_market_window(monkeypatch) -> None:
    settings = make_settings(TRADING_MODE="paper")
    monkeypatch.setattr(
        "poma.cli.should_rebalance_now",
        lambda **_: MarketDecision(False, "2026-07-01", "market is closed"),
    )

    with pytest.raises(RuntimeError, match="market is closed"):
        _assert_rebalance_market_window(settings, allow_outside_market_hours=False)


def test_paper_rebalance_can_be_manually_overridden(monkeypatch) -> None:
    settings = make_settings(TRADING_MODE="paper")
    monkeypatch.setattr(
        "poma.cli.should_rebalance_now",
        lambda **_: MarketDecision(False, "2026-07-01", "market is closed"),
    )

    _assert_rebalance_market_window(settings, allow_outside_market_hours=True)


def test_dry_run_rebalance_skips_market_window_check(monkeypatch) -> None:
    settings = make_settings(TRADING_MODE="dry_run")

    def unexpected_check(**_: object) -> MarketDecision:
        raise AssertionError("dry_run should not query the market window")

    monkeypatch.setattr("poma.cli.should_rebalance_now", unexpected_check)

    _assert_rebalance_market_window(settings, allow_outside_market_hours=False)


def test_is_market_open_during_regular_session() -> None:
    # Wednesday 2026-07-01 15:00 UTC = 11:00 ET, inside regular trading hours.
    assert is_market_open("NASDAQ", datetime(2026, 7, 1, 15, 0, tzinfo=UTC)) is True


def test_is_market_open_false_outside_session_hours() -> None:
    assert is_market_open("NASDAQ", datetime(2026, 7, 1, 5, 20, tzinfo=UTC)) is False
    assert is_market_open("NASDAQ", datetime(2026, 7, 1, 21, 0, tzinfo=UTC)) is False


def test_is_market_open_false_on_weekend_and_holiday() -> None:
    assert is_market_open("NASDAQ", datetime(2026, 7, 4, 15, 0, tzinfo=UTC)) is False  # Saturday
    assert is_market_open("NASDAQ", datetime(2026, 7, 3, 15, 0, tzinfo=UTC)) is False  # July 4th observed


def test_is_market_open_rejects_naive_datetime() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        is_market_open("NASDAQ", datetime(2026, 7, 1, 15, 0))
