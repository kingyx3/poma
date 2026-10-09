from __future__ import annotations

import pandas as pd
import pytest
from conftest import FakeBroker, make_settings
from pydantic import ValidationError

from poma.data import FixtureMarketDataClient, YahooFinanceMarketDataClient
from poma.engine import RebalanceEngine
from poma.models import CurrentPosition, OrderSide, TargetPosition
from poma.portfolio import CURRENT_STRATEGY_NAME
from poma.risk import validate_targets
from poma.strategies import StrategyContext
from poma.strategies.core_etf import (
    NAME,
    CoreEtfStrategy,
    completed_month_closes,
    parse_core_etf_weights,
    trend_is_up,
)


def _monthly_history(month_end_closes: list[float], *, last_day: str) -> pd.Series:
    """Business-day closes where each month trades flat at the given level, ending on last_day."""
    end = pd.Timestamp(last_day)
    months = pd.period_range(end=end.to_period("M"), periods=len(month_end_closes), freq="M")
    pieces = []
    for period, close in zip(months, month_end_closes, strict=True):
        days = pd.bdate_range(period.start_time, min(period.end_time, end))
        pieces.append(pd.Series(close, index=days))
    return pd.concat(pieces)


def _context(settings, history: pd.DataFrame | None = None, capital: float = 9_800.0) -> StrategyContext:
    return StrategyContext(
        strategy_name=NAME,
        allocation_pct=0.98,
        capital_usd=capital,
        current_universe=pd.DataFrame(),
        historical_universe=None,
        settings=settings,
        price_history=history,
    )


def test_parse_core_etf_weights_accepts_basket_and_rejects_bad_input() -> None:
    assert parse_core_etf_weights(" vti=0.6, vxus=0.4 ") == {"VTI": 0.6, "VXUS": 0.4}
    for raw in ["", "VTI", "VTI=0", "VTI=0.7,VXUS=0.4", "VTI=0.5,vti=0.5", "VTI=nan"]:
        with pytest.raises(ValueError):
            parse_core_etf_weights(raw)


def test_settings_validate_core_etf_weights_only_when_allocated() -> None:
    with pytest.raises(ValidationError, match="CORE_ETF_WEIGHTS"):
        make_settings(CORE_ETF_WEIGHTS="VTI=1.5")
    settings = make_settings(
        CORE_ETF_WEIGHTS="VTI=1.5",
        STRATEGY_ALLOCATIONS=f"{CURRENT_STRATEGY_NAME}=0.98,cash=0.02",
    )
    assert settings.core_etf_weights == "VTI=1.5"


def test_default_holds_full_sleeve_in_vti_without_universe_data() -> None:
    strategy = CoreEtfStrategy()
    settings = make_settings()
    requirements = strategy.data_requirements(settings)
    assert requirements.uses_universe is False
    assert requirements.price_history_tickers == ("VTI",)
    assert requirements.price_history_days == 10

    book = strategy.build_targets(_context(settings))

    assert [(t.ticker, t.sleeve_weight, t.target_notional) for t in book.targets] == [("VTI", 1.0, 9_800.0)]
    assert book.targets[0].portfolio_weight == pytest.approx(0.98)
    assert book.diversified_fund_tickers == ("VTI",)
    assert book.warnings == ()


def test_trend_signal_uses_only_completed_months() -> None:
    # Ten completed months rising to 110, then the in-progress month crashes to 50. The partial
    # month must not flip the signal: it only counts once the month has closed.
    closes = [100, 101, 102, 103, 104, 105, 106, 107, 108, 110, 50]
    history = _monthly_history(closes, last_day="2026-10-09")

    month_ends = completed_month_closes(history)
    assert month_ends.index[-1] == pd.Period("2026-09", freq="M")
    assert trend_is_up(history, 10) is True
    assert trend_is_up(history, 11) is None

    falling = _monthly_history([110, 109, 108, 107, 106, 105, 104, 103, 102, 100, 200], last_day="2026-10-09")
    assert trend_is_up(falling, 10) is False


def test_trend_filter_moves_weight_to_safe_asset_or_cash() -> None:
    falling = _monthly_history([110, 109, 108, 107, 106, 105, 104, 103, 102, 100, 99], last_day="2026-10-09")
    rising = _monthly_history([100, 101, 102, 103, 104, 105, 106, 107, 108, 110, 111], last_day="2026-10-09")
    history = pd.DataFrame({"VTI": falling, "VXUS": rising, "SGOV": rising})
    strategy = CoreEtfStrategy()

    with_safe = make_settings(
        CORE_ETF_WEIGHTS="VTI=0.6,VXUS=0.4",
        CORE_ETF_TREND_FILTER="true",
        CORE_ETF_SAFE_ASSET="sgov",
    )
    requirements = strategy.data_requirements(with_safe)
    assert requirements.price_history_tickers == ("VTI", "VXUS", "SGOV")
    assert requirements.price_history_days >= 10 * 31

    book = strategy.build_targets(_context(with_safe, history, capital=10_000.0))
    assert {t.ticker: t.sleeve_weight for t in book.targets} == {"SGOV": 0.6, "VXUS": 0.4}
    assert book.diversified_fund_tickers == ("SGOV", "VTI", "VXUS")
    assert any("VTI: below its trend average" in w for w in book.warnings)

    to_cash = make_settings(CORE_ETF_WEIGHTS="VTI=0.6,VXUS=0.4", CORE_ETF_TREND_FILTER="true")
    book = strategy.build_targets(_context(to_cash, history, capital=10_000.0))
    assert {t.ticker: t.sleeve_weight for t in book.targets} == {"VXUS": 0.4}
    assert any("holding its weight in cash" in w for w in book.warnings)


def test_trend_filter_holds_etf_with_warning_when_history_is_short() -> None:
    settings = make_settings(CORE_ETF_TREND_FILTER="true")
    history = pd.DataFrame({"VTI": _monthly_history([100, 101, 102], last_day="2026-10-09")})

    book = CoreEtfStrategy().build_targets(_context(settings, history))

    assert [t.ticker for t in book.targets] == ["VTI"]
    assert any("fewer than 10 completed months" in w for w in book.warnings)


def test_position_cap_exempts_only_diversified_funds() -> None:
    targets = [TargetPosition("VTI", 0.98, 9_800.0), TargetPosition("AAPL", 0.2, 2_000.0)]
    warnings = validate_targets(targets, max_position_pct=0.10, exempt_tickers=frozenset({"VTI"}))
    assert any("exceed max_position_pct" in w for w in warnings)
    assert validate_targets(targets[:1], 0.10, frozenset({"VTI"})) == []


class _RecordingFixtureClient(FixtureMarketDataClient):
    def __init__(self, *, fail: bool = False) -> None:
        self.universe_calls = 0
        self.history_calls: list[tuple[list[str], int]] = []
        self.fail = fail

    def current_universe_snapshot(self) -> pd.DataFrame:
        self.universe_calls += 1
        return super().current_universe_snapshot()

    def close_price_history(self, tickers: list[str], lookback_days: int) -> pd.DataFrame:
        self.history_calls.append((list(tickers), lookback_days))
        if self.fail:
            raise RuntimeError("yahoo unavailable")
        index = pd.bdate_range(end="2026-10-09", periods=2)
        return pd.DataFrame({ticker: [300.0, 330.0] for ticker in tickers}, index=index)


def test_default_plan_buys_one_etf_order_and_sells_legacy_stocks_without_screener() -> None:
    client = _RecordingFixtureClient()
    broker = FakeBroker(
        positions=[CurrentPosition("AAPL", quantity=1.0, market_value=200.0)],
        cash_usd=9_800.0,
    )
    engine = RebalanceEngine(
        make_settings(TRADING_MODE="paper", IBKR_ACCOUNT="DU1234567"),
        data_client=client,
        broker=broker,
    )

    plan = engine.build_plan("session", "rebalance-x")

    assert client.universe_calls == 0
    assert client.history_calls == [(["VTI"], 10)]
    assert not engine.is_blocked(plan), plan.warnings
    by_ticker = {trade.ticker: trade for trade in plan.trades}
    assert by_ticker["AAPL"].side == OrderSide.SELL
    vti = by_ticker["VTI"]
    assert vti.side == OrderSide.BUY
    assert vti.reference_price == 330.0
    # 98% of $10k at $330 rounds to 30 whole shares.
    assert vti.quantity == 30


def test_plan_blocks_when_etf_prices_cannot_be_loaded() -> None:
    engine = RebalanceEngine(make_settings(), data_client=_RecordingFixtureClient(fail=True), broker=FakeBroker())

    plan = engine.build_plan("session", "rebalance-x")

    assert engine.is_blocked(plan)
    assert any("unable to load price history for VTI" in w for w in plan.warnings)
    assert plan.trades == []


def test_rank_strategy_still_selectable_and_uses_universe() -> None:
    client = _RecordingFixtureClient()
    engine = RebalanceEngine(
        make_settings(STRATEGY_ALLOCATIONS=f"{CURRENT_STRATEGY_NAME}=0.98,cash=0.02"),
        data_client=client,
        broker=FakeBroker(),
    )

    plan = engine.build_plan("session", "rebalance-x")

    assert client.universe_calls == 1
    assert client.history_calls == []
    assert plan.strategy_books[0].strategy_name == CURRENT_STRATEGY_NAME
    assert {t.ticker for t in plan.targets} == {"MSFT", "NVDA", "AAPL", "AMZN"}


def test_yahoo_close_price_history_prefers_adjusted_closes(monkeypatch) -> None:
    class FakeYahoo:
        @staticmethod
        def download(tickers, start=None, end=None, **kwargs):
            columns = pd.MultiIndex.from_product([["VTI", "SGOV"], ["Close", "Adj Close"]])
            return pd.DataFrame(
                [[300.0, 299.0, 100.5, 100.0], [301.0, 301.0, 100.6, 100.6]],
                index=pd.to_datetime(["2026-10-08", "2026-10-09"]),
                columns=columns,
            )

    monkeypatch.setattr("poma.data._load_yfinance", lambda: (FakeYahoo, object))
    client = YahooFinanceMarketDataClient(make_settings(DATA_PROVIDER="yahoo"))

    history = client.close_price_history(["VTI", "SGOV"], 10)

    assert list(history.columns) == ["VTI", "SGOV"]
    assert history["VTI"].tolist() == [299.0, 301.0]
    assert history["SGOV"].iloc[-1] == 100.6


def test_fixture_price_history_supports_dry_runs() -> None:
    history = FixtureMarketDataClient().close_price_history(["VTI"], 400)
    assert len(history) > 250
    assert history["VTI"].is_monotonic_increasing
