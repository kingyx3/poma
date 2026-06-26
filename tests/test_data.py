from __future__ import annotations

from typing import Any

import requests

from conftest import make_settings

from poma.data import FmpMarketDataClient


class FakeResponse:
    def __init__(self, payload: Any, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.headers: dict[str, str] = {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}", response=self)

    def json(self) -> Any:
        return self._payload


def _client(monkeypatch, router, **overrides) -> FmpMarketDataClient:
    calls: list[str] = []

    def fake_get(self, url, params=None, timeout=None):
        path = url.rsplit("/", maxsplit=1)[-1]
        calls.append(path)
        response = router(path, params or {})
        if isinstance(response, FakeResponse):
            return response
        return FakeResponse(response)

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
            return [
                {"symbol": "AAPL", "price": 195.0},
                {"symbol": "MSFT", "price": 410.0},
            ]
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


def test_fmp_constituents_fall_back_to_legacy_endpoint(monkeypatch) -> None:
    def router(path, params):
        if path == "sp500-constituent":
            return FakeResponse({"error": "gated"}, status_code=402)
        if path == "sp500_constituent":
            return [{"symbol": "AAPL"}]
        if path == "market-capitalization-batch":
            return [{"symbol": "AAPL", "marketCap": 3000}]
        if path == "batch-quote-short":
            return [{"symbol": "AAPL", "price": 195.0}]
        raise AssertionError(f"unexpected path {path}")

    client = _client(monkeypatch, router)
    frame = client.current_universe_snapshot()

    assert client.calls[:2] == [  # type: ignore[attr-defined]
        "sp500-constituent",
        "sp500_constituent",
    ]
    assert frame["ticker"].tolist() == ["AAPL"]
