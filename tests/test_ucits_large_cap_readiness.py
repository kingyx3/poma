"""Paper-only readiness tests for USD London Irish large-cap UCITS ETF lines."""

from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd
import pytest
from conftest import make_settings
from test_broker_order_lifecycle import (
    FakeContract,
    FakeIB,
    FakeMarketData,
    FakePortfolioItem,
    _settings,
)

from poma.broker import IbkrBroker, _stock_contract
from poma.data import YahooFinanceMarketDataClient
from poma.ucits_instruments import price_symbol, ucits_listing


@pytest.mark.parametrize(
    ("ticker", "yahoo", "isin"),
    [
        ("SP1", "SP1.L", "IE000MU0FDZ8"),
        ("USMC", "USMC.L", "IE0008M1R3N4"),
    ],
)
def test_usd_lse_etf_identity_is_explicit(ticker: str, yahoo: str, isin: str) -> None:
    listing = ucits_listing(ticker)
    assert listing is not None
    assert listing.isin == isin
    assert listing.primary_exchange == "LSEETF"
    assert listing.currency == "USD"
    assert price_symbol(ticker) == yahoo
    contract = _stock_contract(ticker, "SMART")
    assert contract.symbol == ticker
    assert contract.currency == "USD"
    assert contract.primaryExchange == "LSEETF"


def test_us_equities_continue_using_legacy_unambiguous_contracts() -> None:
    assert price_symbol("VTI") == "VTI"
    assert ucits_listing("AAPL") is None
    contract = _stock_contract("VTI", "SMART")
    assert contract.symbol == "VTI"
    assert contract.primaryExchange == ""


def test_yahoo_lse_usd_etf_symbol_maps_back_to_strategy_ticker(monkeypatch: pytest.MonkeyPatch) -> None:
    class YahooStub:
        downloaded: str = ""

        @classmethod
        def download(cls, tickers, **_kwargs):
            cls.downloaded = tickers
            columns = pd.MultiIndex.from_product([["SP1.L", "USMC.L"], ["Adj Close"]])
            return pd.DataFrame(
                [[11.0, 32.0], [11.5, 33.0]],
                columns=columns,
                index=pd.to_datetime(["2026-10-08", "2026-10-09"]),
            )

    monkeypatch.setattr("poma.data._load_yfinance", lambda: (YahooStub, object))
    client = YahooFinanceMarketDataClient(make_settings())
    history = client.close_price_history(["SP1", "USMC"], 10)

    assert YahooStub.downloaded == "SP1.L USMC.L"
    assert list(history.columns) == ["SP1", "USMC"]
    assert history.iloc[-1]["SP1"] == 11.5
    assert history.iloc[-1]["USMC"] == 33.0


@pytest.mark.parametrize("ticker", ["SP1", "USMC"])
def test_lse_quote_does_not_request_us_only_iex_venue(monkeypatch: pytest.MonkeyPatch, ticker: str) -> None:
    fake = FakeIB(
        market_data_by_symbol={
            ticker: FakeMarketData(
                contract=FakeContract(symbol=ticker),
                bid=11.50,
                ask=11.53,
                last=11.52,
                time=datetime.now(UTC),
            )
        }
    )
    monkeypatch.setattr("poma.broker.IB", lambda: fake)
    quote = IbkrBroker(_settings(monkeypatch)).execution_quotes([ticker])[ticker]
    assert fake.requested_market_data_contracts == [(ticker, "SMART")]
    assert quote.bid == 11.50
    assert quote.ask == 11.53


def test_lse_usd_positions_are_retained_in_broker_account(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeIB(
        portfolio_items=[
            FakePortfolioItem(contract=FakeContract(symbol="SP1"), position=7, marketValue=80.5),
        ]
    )
    monkeypatch.setattr("poma.broker.IB", lambda: fake)
    positions, warnings = IbkrBroker(_settings(monkeypatch))._positions_from_ib(fake)
    assert [(position.ticker, position.quantity, position.market_value) for position in positions] == [
        ("SP1", 7.0, 80.5)
    ]
    assert warnings == []


@pytest.mark.parametrize("ticker", ["SP1", "USMC"])
def test_replacement_limit_order_preserves_lse_contract_identity(monkeypatch: pytest.MonkeyPatch, ticker: str) -> None:
    """Simulate the actual broker placement helper without sending a real order."""
    from poma.models import OrderSide

    fake = FakeIB()
    monkeypatch.setattr("poma.broker.IB", lambda: fake)
    broker = IbkrBroker(_settings(monkeypatch))
    snapshot = broker._place_limit_order(
        fake, ticker, OrderSide.BUY, 2.0, 11.53, f"poma:paper:0:{ticker}:BUY:r1"
    )

    contract, order = fake.placed_orders[0]
    assert contract.symbol == ticker
    assert contract.primaryExchange == "LSEETF"
    assert contract.currency == "USD"
    assert order.action == "BUY"
    assert order.lmtPrice == 11.53
    assert snapshot.order_ref == f"poma:paper:0:{ticker}:BUY:r1"
    assert snapshot.raw_status == "Submitted"


def test_core_etf_uses_usmc_in_live_mode_with_lse_calendar() -> None:
    settings = make_settings(
        TRADING_MODE="live",
        IBKR_ACCOUNT="U1234567",
        ALLOW_DELAYED_EXECUTION_QUOTES=False,
    )
    assert settings.market_calendar == "LSE"
    assert settings.rebalance_after_open_minutes == 390


@pytest.mark.parametrize("mode", ["paper", "live"])
def test_core_etf_rejects_wrong_calendar_or_early_market_window(mode: str) -> None:
    with pytest.raises(ValueError, match="MARKET_CALENDAR=LSE"):
        make_settings(TRADING_MODE=mode, MARKET_CALENDAR="NASDAQ")
    with pytest.raises(ValueError, match="REBALANCE_AFTER_OPEN_MINUTES>=390"):
        make_settings(TRADING_MODE=mode, REBALANCE_AFTER_OPEN_MINUTES=10)


def test_us_equity_only_strategy_can_use_us_calendar() -> None:
    settings = make_settings(
        TRADING_MODE="live",
        INVESTMENT_STRATEGY_WEIGHTS="rank_velocity_size_equal_weight=1.0",
        IBKR_ACCOUNT="U1234567",
        MARKET_CALENDAR="NASDAQ",
        REBALANCE_AFTER_OPEN_MINUTES=10,
        ALLOW_DELAYED_EXECUTION_QUOTES=False,
    )
    assert settings.market_calendar == "NASDAQ"


def test_core_etf_is_default_for_paper_dry_run_and_live() -> None:
    for mode in ("dry_run", "paper", "live"):
        settings = make_settings(TRADING_MODE=mode)
        assert settings.strategy_allocation_map() == pytest.approx({"USMC": 0.98, "cash": 0.02})
        assert settings.market_calendar == "LSE"
