from __future__ import annotations

import pytest
from conftest import make_settings

from poma.cli import _assert_rebalance_market_window
from poma.market_calendar import MarketDecision


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
