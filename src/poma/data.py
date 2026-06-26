from __future__ import annotations

import time
from datetime import UTC, date, datetime
from typing import Any, Protocol

import pandas as pd
import requests

from poma.config import Settings

FMP_MAX_RETRIES = 4
FMP_MAX_BACKOFF_SECONDS = 30

# FMP "stable" constituent endpoints return membership only (symbol/name/sector) — no market
# cap or price. Market cap and price come from the (batch) market-cap and quote endpoints.
FMP_CONSTITUENT_ENDPOINTS = {
    "nasdaq100": "nasdaq-constituent",
    "sp500": "sp500-constituent",
}
FMP_BATCH_SIZE = 100
_MARKET_CAP_KEYS = ("marketCap", "marketCapitalization", "market_cap")


class MarketDataClient(Protocol):
    def current_universe_snapshot(self) -> pd.DataFrame:
        """Return columns: ticker, market_cap, price."""


def _chunked(items: list[str], size: int) -> list[list[str]]:
    return [items[index : index + size] for index in range(0, len(items), size)]


def _market_cap_value(row: dict[str, Any]) -> Any:
    for key in _MARKET_CAP_KEYS:
        value = row.get(key)
        if value is not None:
            return value
    return None


class FmpMarketDataClient:
    """FMP "stable" adapter.

    Universe membership comes from the constituent endpoint; market caps and prices come from
    the batch market-cap and batch quote endpoints (the constituent endpoint carries neither).
    """

    def __init__(self, settings: Settings) -> None:
        if not settings.fmp_api_key:
            raise ValueError("FMP_API_KEY is required when DATA_PROVIDER=fmp")
        if settings.universe not in FMP_CONSTITUENT_ENDPOINTS:
            supported = ", ".join(sorted(FMP_CONSTITUENT_ENDPOINTS))
            raise ValueError(f"unsupported FMP universe={settings.universe}; supported={supported}")
        self.settings = settings
        self._session = requests.Session()

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        merged = {"apikey": self.settings.fmp_api_key, **(params or {})}
        url = f"{self.settings.fmp_base_url}/{path.lstrip('/')}"
        response = None
        for attempt in range(FMP_MAX_RETRIES):
            response = self._session.get(url, params=merged, timeout=30)
            if getattr(response, "status_code", 200) != 429:
                break
            if attempt < FMP_MAX_RETRIES - 1:
                retry_after = response.headers.get("Retry-After")
                delay = float(retry_after) if retry_after else float(2**attempt)
                time.sleep(min(delay, FMP_MAX_BACKOFF_SECONDS))
        response.raise_for_status()
        return response.json()

    def _constituent_symbols(self) -> list[str]:
        rows = self._get(FMP_CONSTITUENT_ENDPOINTS[self.settings.universe])
        symbols = [str(row.get("symbol", "")).upper().strip() for row in rows or []]
        symbols = [symbol for symbol in symbols if symbol]
        if not symbols:
            raise ValueError("FMP constituent endpoint returned no symbols")
        return list(dict.fromkeys(symbols))  # de-duplicate, preserve order

    def current_universe_snapshot(self) -> pd.DataFrame:
        symbols = self._constituent_symbols()
        caps: dict[str, Any] = {}
        prices: dict[str, Any] = {}
        for chunk in _chunked(symbols, FMP_BATCH_SIZE):
            joined = ",".join(chunk)
            for row in self._get("market-capitalization-batch", {"symbols": joined}) or []:
                symbol = str(row.get("symbol", "")).upper().strip()
                cap = _market_cap_value(row)
                if symbol and cap is not None:
                    caps[symbol] = cap
            for row in self._get("batch-quote-short", {"symbols": joined}) or []:
                symbol = str(row.get("symbol", "")).upper().strip()
                price = row.get("price")
                if symbol and price is not None:
                    prices[symbol] = price

        records = [
            {"ticker": symbol, "market_cap": caps[symbol], "price": prices[symbol]}
            for symbol in symbols
            if symbol in caps and symbol in prices
        ]
        return _normalise_snapshot(records)


def _normalise_snapshot(rows: list[dict[str, Any]]) -> pd.DataFrame:
    if not rows:
        raise ValueError("market data provider returned no rows")

    frame = pd.DataFrame(rows)
    rename_map = {
        "symbol": "ticker",
        "ticker": "ticker",
        "marketCap": "market_cap",
        "marketCapitalization": "market_cap",
        "market_cap": "market_cap",
        "price": "price",
    }
    valid_renames = {key: value for key, value in rename_map.items() if key in frame.columns}
    frame = frame.rename(columns=valid_renames)
    required = {"ticker", "market_cap"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"provider snapshot missing required columns: {sorted(missing)}")
    columns = [column for column in ["ticker", "market_cap", "price"] if column in frame]
    frame = frame[columns].copy()
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
                {
                    "ticker": "MSFT",
                    "market_cap": 3_100_000_000_000,
                    "price": 420,
                    "as_of": today,
                },
                {
                    "ticker": "NVDA",
                    "market_cap": 3_000_000_000_000,
                    "price": 125,
                    "as_of": today,
                },
                {
                    "ticker": "AAPL",
                    "market_cap": 2_900_000_000_000,
                    "price": 195,
                    "as_of": today,
                },
                {
                    "ticker": "AMZN",
                    "market_cap": 1_900_000_000_000,
                    "price": 180,
                    "as_of": today,
                },
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
