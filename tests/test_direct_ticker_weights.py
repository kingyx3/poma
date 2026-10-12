"""Direct investment ticker allocations, alongside named algorithm strategies."""
from __future__ import annotations

import pandas as pd
import pytest
from conftest import FakeBroker, make_settings

from poma.data import FixtureMarketDataClient
from poma.engine import RebalanceEngine
from poma.strategies import default_registry


def test_direct_usmc_lowercase_is_canonical_and_reserves_cash() -> None:
    settings = make_settings(INVESTMENT_STRATEGY_WEIGHTS="usmc=1.0")
    assert settings.strategy_allocation_map() == pytest.approx({"USMC": 0.98, "cash": 0.02})
    assert settings.market_calendar == "LSE"
    assert settings.rebalance_after_open_minutes == 390


def test_direct_multi_etf_weights_are_of_invested_portfolio() -> None:
    settings = make_settings(
        INVESTMENT_STRATEGY_WEIGHTS="usmc=0.8,sp1=0.2",
        CASH_TARGET_PCT=0.02,
    )
    assert settings.strategy_allocation_map() == pytest.approx({
        "USMC": 0.784, "SP1": 0.196, "cash": 0.02,
    })


def test_direct_sp1_only_still_requires_london_market_calendar() -> None:
    settings = make_settings(INVESTMENT_STRATEGY_WEIGHTS="sp1=1.0")
    assert settings.strategy_allocation_map() == pytest.approx({"SP1": 0.98, "cash": 0.02})
    assert settings.market_calendar == "LSE"
    with pytest.raises(ValueError, match="MARKET_CALENDAR=LSE"):
        make_settings(INVESTMENT_STRATEGY_WEIGHTS="SP1=1.0", MARKET_CALENDAR="NASDAQ")
    with pytest.raises(ValueError, match="REBALANCE_AFTER_OPEN_MINUTES>=390"):
        make_settings(TRADING_MODE="paper", INVESTMENT_STRATEGY_WEIGHTS="SP1=1.0",
                      REBALANCE_AFTER_OPEN_MINUTES=10)


def test_reject_duplicate_direct_symbols_even_when_case_differs() -> None:
    with pytest.raises(ValueError, match="duplicate investment strategy allocation"):
        make_settings(INVESTMENT_STRATEGY_WEIGHTS="USMC=0.8,usmc=0.2")


def test_unregistered_ticker_is_fail_closed_not_guessed() -> None:
    with pytest.raises(ValueError, match="unregistered strategies"):
        make_settings(INVESTMENT_STRATEGY_WEIGHTS="USMC=0.8,XXX=0.2")


def test_existing_algorithmic_sleeve_can_mix_with_direct_ticker() -> None:
    settings = make_settings(
        INVESTMENT_STRATEGY_WEIGHTS="usmc=0.8,rank_velocity_size_equal_weight=0.2",
    )
    assert settings.strategy_allocation_map() == pytest.approx({
        "USMC": 0.784, "rank_velocity_size_equal_weight": 0.196,
        "cash": 0.02,
    })


class _DirectPriceClient(FixtureMarketDataClient):
    def __init__(self) -> None:
        self.universe_calls = 0
        self.history_requests: list[list[str]] = []

    def current_universe_snapshot(self):  # noqa: ANN201
        self.universe_calls += 1
        return super().current_universe_snapshot()

    def close_price_history(self, tickers: list[str], lookback_days: int) -> pd.DataFrame:
        self.history_requests.append(list(tickers))
        index = pd.bdate_range(end="2026-10-09", periods=2)
        return pd.DataFrame({ticker: [99.0, 100.0] for ticker in tickers}, index=index)


def test_engine_builds_two_direct_fund_books_without_universe_screener() -> None:
    prices = _DirectPriceClient()
    settings = make_settings(
        INVESTMENT_STRATEGY_WEIGHTS="USMC=80%,SP1=20%",
        DRY_RUN_PORTFOLIO_VALUE_USD=10000,
    )
    engine = RebalanceEngine(settings, data_client=prices, broker=FakeBroker())
    plan = engine.build_plan("session", "run")

    assert prices.universe_calls == 0
    assert len(prices.history_requests) == 1
    assert set(prices.history_requests[0]) == {"USMC", "SP1"}
    assert {book.strategy_name for book in plan.strategy_books} == {"USMC", "SP1"}
    by_ticker = {target.ticker: target for target in plan.targets}
    assert by_ticker["USMC"].target_notional == pytest.approx(7840)
    assert by_ticker["SP1"].target_notional == pytest.approx(1960)
    assert plan.cash_sleeve_usd == pytest.approx(200)
    assert not engine.is_blocked(plan), plan.warnings


def test_direct_fund_strategy_registry_has_usmc_sp1() -> None:
    registry = default_registry()
    for ticker in ("USMC", "SP1"):
        strategy = registry.get(ticker)
        assert strategy.name == ticker
        requirements = strategy.data_requirements(make_settings())
        assert requirements.price_history_tickers == (ticker,)
