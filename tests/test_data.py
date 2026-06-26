from __future__ import annotations

from typing import Any

from conftest import make_settings

from poma.data import FmpMarketDataClient


class FakeResponse:
    def __init__(self, payload: Any) -> None:
        self._payload = payload

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
        make_settings(DATA_PROVIDER="fmp", FMP_API_KEY="key", **overrides)
    )
    client.calls = calls  # type: ignore[attr-defined]
    return client


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


def test_previous_snapshot_uses_latest_historical_market_cap(monkeypatch) -> None:
    def router(path, params):
        if path == "sp500-constituent":
            return [{"symbol": "AAPL"}, {"symbol": "MSFT"}]
        if path == "historical-market-capitalization":
            base = 100 if params["symbol"] == "AAPL" else 50
            return [
                {"date": "2026-03-20", "marketCap": base},
                {"date": "2026-03-27", "marketCap": base + 10},
            ]
        raise AssertionError(f"unexpected path {path}")

    client = _client(monkeypatch, router)
    frame = client.previous_universe_snapshot(90)

    # Latest row by date wins; previous snapshot needs no price column.
    assert frame.set_index("ticker").loc["AAPL", "market_cap"] == 110
    assert "price" not in frame.columns


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
