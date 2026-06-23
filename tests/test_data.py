from __future__ import annotations

from typing import Any

from poma.config import Settings
from poma.data import FmpMarketDataClient


class FakeResponse:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    def raise_for_status(self) -> None:
        return None

    def json(self) -> list[dict[str, Any]]:
        return self.rows


def test_fmp_uses_sp500_endpoints_by_default(monkeypatch) -> None:
    called_paths: list[str] = []

    def fake_get(url: str, **_: Any) -> FakeResponse:
        called_paths.append(url.rsplit("/", maxsplit=1)[-1])
        return FakeResponse(
            [
                {"symbol": "A", "marketCap": 200, "price": 20},
                {"symbol": "B", "marketCap": 100, "price": 10},
            ]
        )

    monkeypatch.setattr("poma.data.requests.get", fake_get)
    settings = Settings(
        TELEGRAM_BOT_TOKEN="token",
        TELEGRAM_CHAT_ID="chat",
        DATA_PROVIDER="fmp",
        FMP_API_KEY="key",
    )
    client = FmpMarketDataClient(settings)

    current = client.current_universe_snapshot()
    previous = client.previous_universe_snapshot(90)

    assert called_paths == ["sp500-constituent", "historical-sp500-constituent"]
    assert current["ticker"].tolist() == ["A", "B"]
    assert previous["ticker"].tolist() == ["A", "B"]


def test_fmp_still_supports_nasdaq100_endpoints(monkeypatch) -> None:
    called_paths: list[str] = []

    def fake_get(url: str, **_: Any) -> FakeResponse:
        called_paths.append(url.rsplit("/", maxsplit=1)[-1])
        return FakeResponse([{"symbol": "A", "marketCap": 100, "price": 10}])

    monkeypatch.setattr("poma.data.requests.get", fake_get)
    settings = Settings(
        TELEGRAM_BOT_TOKEN="token",
        TELEGRAM_CHAT_ID="chat",
        DATA_PROVIDER="fmp",
        FMP_API_KEY="key",
        UNIVERSE="nasdaq100",
    )
    client = FmpMarketDataClient(settings)

    client.current_universe_snapshot()
    client.previous_universe_snapshot(90)

    assert called_paths == ["nasdaq-constituent", "historical-nasdaq-constituent"]
