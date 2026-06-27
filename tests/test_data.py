from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd

from conftest import make_settings

from poma.data import FmpMarketDataClient, YahooFinanceMarketDataClient


class FakeResponse:
    def __init__(self, payload: Any) -> None:
        self._payload = payload
        self.status_code = 200
        self.headers: dict[str, str] = {}

    def raise_for_status(self) -> None:
        return None

    def json(self) -> Any:
        return self._payload


def _client(monkeypatch, router, **overrides) -> FmpMarketDataClient:
    calls: list[str] = []

    def fake_get(self, url, params=None, timeout=None):
        path = url.rsplit("/", maxsplit=1)[-1]
        calls.append(path)
        return FakeResponse(router(path, params or {}))

    monkeypatch.setattr("poma.data.requests.Session.get", fake_get)
    client = FmpMarketDataClient(
        make_settings(DATA_PROVIDER="fmp", FMP_API_KEY="key", UNIVERSE="sp500", **overrides)
    )
    client.calls = calls  # type: ignore[attr-defined]
    return client


class FakeEquityQuery:
    def __init__(self, operator: str, operands: list[Any]) -> None:
        self.operator = operator
        self.operands = operands


class FakeYahoo:
    screen_calls: list[dict[str, Any]] = []
    downloaded_tickers: str | None = None

    @classmethod
    def screen(cls, query, offset=None, size=None, sortField=None, sortAsc=None):
        cls.screen_calls.append(
            {"offset": offset, "size": size, "sortField": sortField, "sortAsc": sortAsc}
        )
        return {
            "quotes": [
                {
                    "symbol": "aapl",
                    "shortName": "Apple",
                    "exchange": "NMS",
                    "intradaymarketcap": 3000,
                    "regularMarketPrice": 30,
                    "floatShares": 95,
                },
                {
                    "symbol": "MSFT",
                    "shortName": "Microsoft",
                    "exchange": "NMS",
                    "intradaymarketcap": 2000,
                    "regularMarketPrice": 20,
                },
            ]
        }

    @classmethod
    def download(cls, tickers, start=None, end=None, **kwargs):
        cls.downloaded_tickers = tickers
        columns = pd.MultiIndex.from_product([["AAPL", "MSFT"], ["Close"]])
        return pd.DataFrame(
            [[29.0, 18.0], [30.0, 20.0]],
            index=pd.to_datetime(["2026-01-01", "2026-01-02"]),
            columns=columns,
        )


def test_current_snapshot_merges_constituents_caps_and_prices(monkeypatch) -> None:
    def router(path, params):
        if path == "sp500-constituent":
            return [{"symbol": "AAPL"}, {"symbol": "MSFT"}, {"symbol": "NOPRICE"}]
        if path == "market-capitalization-batch":
            return [
                {"symbol": "AAPL", "marketCap": 3000},
                {"symbol": "MSFT", "marketCap": 2000},
                {"symbol": "NOPRICE", "marketCap": 1000},
            ]
        if path == "batch-quote-short":
            return [{"symbol": "AAPL", "price": 195.0}, {"symbol": "MSFT", "price": 410.0}]
        raise AssertionError(f"unexpected path {path}")

    client = _client(monkeypatch, router)
    frame = client.current_universe_snapshot()

    # The constituent endpoint carries no market cap; caps/prices come from the batch endpoints.
    assert "market-capitalization-batch" in client.calls  # type: ignore[attr-defined]
    assert "batch-quote-short" in client.calls  # type: ignore[attr-defined]
    # NOPRICE has a cap but no price, so it is dropped (price is needed for trade sizing).
    assert frame["ticker"].tolist() == ["AAPL", "MSFT"]
    assert frame.set_index("ticker").loc["AAPL", "market_cap"] == 3000
    assert frame.set_index("ticker").loc["MSFT", "price"] == 410.0


def test_fmp_universe_selects_constituent_endpoint(monkeypatch) -> None:
    def router(path, params):
        if path.endswith("constituent"):
            return [{"symbol": "AAPL"}]
        if path == "market-capitalization-batch":
            return [{"symbol": "AAPL", "marketCap": 3000}]
        if path == "batch-quote-short":
            return [{"symbol": "AAPL", "price": 195.0}]
        raise AssertionError(f"unexpected path {path}")

    client = _client(monkeypatch, router, UNIVERSE="nasdaq100")
    client.current_universe_snapshot()
    assert "nasdaq-constituent" in client.calls  # type: ignore[attr-defined]


def test_yahoo_screener_normalizes_market_cap_price_and_share_fields(monkeypatch) -> None:
    FakeYahoo.screen_calls = []
    monkeypatch.setattr("poma.data._load_yfinance", lambda: (FakeYahoo, FakeEquityQuery))
    client = YahooFinanceMarketDataClient(
        make_settings(
            DATA_PROVIDER="yahoo",
            UNIVERSE="us_top_market_cap",
            YAHOO_SCREENER_LIMIT=2,
        )
    )

    frame = client.current_universe_snapshot()

    assert FakeYahoo.screen_calls[0]["size"] == 2
    assert FakeYahoo.screen_calls[0]["sortField"] == "intradaymarketcap"
    assert frame["ticker"].tolist() == ["AAPL", "MSFT"]
    assert frame.set_index("ticker").loc["AAPL", "float_shares"] == 95
    assert frame.set_index("ticker").loc["MSFT", "shares_outstanding"] == 100


def test_yahoo_historical_snapshots_estimate_market_cap_from_close_prices(monkeypatch) -> None:
    monkeypatch.setattr("poma.data._load_yfinance", lambda: (FakeYahoo, FakeEquityQuery))
    client = YahooFinanceMarketDataClient(
        make_settings(DATA_PROVIDER="yahoo", UNIVERSE="us_top_market_cap")
    )
    current = pd.DataFrame(
        [
            {"ticker": "AAPL", "market_cap": 3000, "price": 30, "shares_outstanding": 100},
            {"ticker": "MSFT", "market_cap": 2000, "price": 20, "shares_outstanding": 100},
        ]
    )

    snapshots = client.historical_universe_snapshots(
        current,
        lookback_days=2,
        end_date=date(2026, 1, 2),
    )

    jan_1 = snapshots[date(2026, 1, 1)].set_index("ticker")
    assert jan_1.loc["AAPL", "price"] == 29.0
    assert jan_1.loc["AAPL", "market_cap"] == 2900.0
    assert jan_1.loc["MSFT", "market_cap"] == 1800.0
