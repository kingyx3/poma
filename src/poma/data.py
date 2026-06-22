from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any, Protocol

import pandas as pd
import requests

from poma.config import Settings


class MarketDataClient(Protocol):
    def current_universe_snapshot(self) -> pd.DataFrame:
        """Return columns: ticker, market_cap, price."""

    def previous_universe_snapshot(self, periods_ago: int) -> pd.DataFrame:
        """Return columns: ticker, market_cap, price for the comparison period."""


class FmpMarketDataClient:
    """Thin FMP adapter.

    The exact data plan and endpoint availability can differ by subscription. Keep endpoints
    configurable, validate provider output in dry-run mode, and backfill data before trusting
    historical backtests.
    """

    def __init__(self, settings: Settings) -> None:
        if not settings.fmp_api_key:
            raise ValueError("FMP_API_KEY is required when DATA_PROVIDER=fmp")
        self.settings = settings

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        merged = {"apikey": self.settings.fmp_api_key, **(params or {})}
        response = requests.get(f"{self.settings.fmp_base_url}/{path.lstrip('/')}", params=merged, timeout=30)
        response.raise_for_status()
        return response.json()

    def current_universe_snapshot(self) -> pd.DataFrame:
        rows = self._get("nasdaq-constituent")
        return _normalise_snapshot(rows)

    def previous_universe_snapshot(self, periods_ago: int) -> pd.DataFrame:
        # Provider-specific historical constituent and market-cap support varies. The default
        # implementation expects a configured stable endpoint that returns historical snapshots.
        rows = self._get("historical-nasdaq-constituent", {"periodsAgo": periods_ago})
        return _normalise_snapshot(rows)


def _normalise_snapshot(rows: list[dict[str, Any]]) -> pd.DataFrame:
    if not rows:
        raise ValueError("market data provider returned no rows")

    frame = pd.DataFrame(rows)
    rename_map = {
        "symbol": "ticker",
        "ticker": "ticker",
        "marketCap": "market_cap",
        "market_cap": "market_cap",
        "price": "price",
    }
    frame = frame.rename(columns={k: v for k, v in rename_map.items() if k in frame.columns})
    required = {"ticker", "market_cap"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"provider snapshot missing required columns: {sorted(missing)}")
    frame = frame[[c for c in ["ticker", "market_cap", "price"] if c in frame.columns]].copy()
    frame["ticker"] = frame["ticker"].astype(str).str.upper().str.strip()
    frame["market_cap"] = pd.to_numeric(frame["market_cap"], errors="coerce")
    frame = frame.dropna(subset=["ticker", "market_cap"])
    frame = frame[frame["market_cap"] > 0]
    frame = frame.drop_duplicates(subset=["ticker"], keep="first")
    if frame.empty:
        raise ValueError("provider snapshot had no valid market-cap rows")
    return frame


class FixtureMarketDataClient:
    """Deterministic provider used for local dry-runs and tests."""

    def current_universe_snapshot(self) -> pd.DataFrame:
        today = date.today()
        return pd.DataFrame(
            [
                {"ticker": "MSFT", "market_cap": 3_100_000_000_000, "price": 420, "as_of": today},
                {"ticker": "NVDA", "market_cap": 3_000_000_000_000, "price": 125, "as_of": today},
                {"ticker": "AAPL", "market_cap": 2_900_000_000_000, "price": 195, "as_of": today},
                {"ticker": "AMZN", "market_cap": 1_900_000_000_000, "price": 180, "as_of": today},
            ]
        )

    def previous_universe_snapshot(self, periods_ago: int) -> pd.DataFrame:
        _ = periods_ago
        return pd.DataFrame(
            [
                {"ticker": "AAPL", "market_cap": 3_000_000_000_000, "price": 200},
                {"ticker": "MSFT", "market_cap": 2_950_000_000_000, "price": 400},
                {"ticker": "NVDA", "market_cap": 2_700_000_000_000, "price": 110},
                {"ticker": "AMZN", "market_cap": 1_950_000_000_000, "price": 185},
            ]
        )


def build_data_client(settings: Settings) -> MarketDataClient:
    if settings.data_provider == "fixture":
        return FixtureMarketDataClient()
    if settings.data_provider == "fmp":
        return FmpMarketDataClient(settings)
    raise ValueError(f"unsupported DATA_PROVIDER={settings.data_provider}")


def utc_run_id(prefix: str = "rebalance") -> str:
    return f"{prefix}-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
