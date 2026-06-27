from __future__ import annotations

import time
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
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
YAHOO_SUPPORTED_UNIVERSES = {"us_top_market_cap", "us_top500"}
YAHOO_EXCHANGES = ("NMS", "NYQ", "ASE")
YAHOO_DOWNLOAD_BATCH_SIZE = 100
_MARKET_CAP_KEYS = ("marketCap", "marketCapitalization", "market_cap", "intradaymarketcap")
_PRICE_KEYS = ("price", "regularMarketPrice", "intradayprice")
_FLOAT_KEYS = ("floatShares", "float_shares")
_SHARES_KEYS = ("sharesOutstanding", "shares_outstanding")


class MarketDataClient(Protocol):
    def current_universe_snapshot(self) -> pd.DataFrame:
        """Return a normalized snapshot with ticker, market_cap, and price columns."""


class HistoricalMarketDataClient(MarketDataClient, Protocol):
    def historical_universe_snapshots(
        self,
        current_snapshot: pd.DataFrame,
        lookback_days: int,
        end_date: date | None = None,
    ) -> dict[date, pd.DataFrame]:
        """Return daily normalized snapshots for a lookback window when supported."""


def _chunked(items: list[str], size: int) -> list[list[str]]:
    return [items[index : index + size] for index in range(0, len(items), size)]


def _first_value(row: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = row.get(key)
        if value is not None:
            return value
    return None


def _market_cap_value(row: dict[str, Any]) -> Any:
    return _first_value(row, _MARKET_CAP_KEYS)


def _load_yfinance() -> tuple[Any, Any]:
    try:
        import yfinance as yf
        from yfinance import EquityQuery
    except ImportError as exc:  # pragma: no cover - exercised only when dependency is absent
        raise RuntimeError(
            "DATA_PROVIDER=yahoo requires the optional yfinance dependency to be installed"
        ) from exc
    return yf, EquityQuery


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
            {
                "ticker": symbol,
                "market_cap": caps[symbol],
                "price": prices[symbol],
                "source": "fmp",
                "as_of": date.today().isoformat(),
            }
            for symbol in symbols
            if symbol in caps and symbol in prices
        ]
        return _normalise_snapshot(records, require_price=True)


class YahooFinanceMarketDataClient:
    """Yahoo Finance adapter isolated behind the same normalized provider contract.

    Yahoo/yfinance is an unofficial data source. It is useful for low-cost personal research and
    dry-run/paper validation, but production trading can switch providers by adding another
    MarketDataClient implementation without changing strategy or engine code.
    """

    def __init__(self, settings: Settings) -> None:
        if settings.universe not in YAHOO_SUPPORTED_UNIVERSES:
            supported = ", ".join(sorted(YAHOO_SUPPORTED_UNIVERSES))
            raise ValueError(f"unsupported Yahoo universe={settings.universe}; supported={supported}")
        self.settings = settings
        self._yf, self._equity_query = _load_yfinance()

    def _largest_us_equities_query(self) -> Any:
        query_cls = self._equity_query
        return query_cls(
            "and",
            [
                query_cls("eq", ["region", "us"]),
                query_cls("is-in", ["exchange", *YAHOO_EXCHANGES]),
                query_cls("gt", ["intradaymarketcap", 0]),
                query_cls("gt", ["intradayprice", 1]),
            ],
        )

    @staticmethod
    def _extract_quotes(response: dict[str, Any]) -> list[dict[str, Any]]:
        if not response:
            return []
        if "quotes" in response:
            return list(response["quotes"] or [])
        result = response.get("finance", {}).get("result", [])
        if result and "quotes" in result[0]:
            return list(result[0]["quotes"] or [])
        result = response.get("result", [])
        if result and "quotes" in result[0]:
            return list(result[0]["quotes"] or [])
        raise ValueError(f"unexpected Yahoo screen response shape: {sorted(response)}")

    def current_universe_snapshot(self) -> pd.DataFrame:
        limit = int(self.settings.yahoo_screener_limit)
        page_size = min(int(self.settings.yahoo_screener_page_size), 250)
        rows: list[dict[str, Any]] = []
        query = self._largest_us_equities_query()

        for offset in range(0, limit, page_size):
            response = self._yf.screen(
                query,
                offset=offset,
                size=min(page_size, limit - offset),
                sortField="intradaymarketcap",
                sortAsc=False,
            )
            rows.extend(self._extract_quotes(response))

        records = []
        for row in rows:
            symbol = str(row.get("symbol", "")).upper().strip()
            market_cap = _market_cap_value(row)
            price = _first_value(row, _PRICE_KEYS)
            shares_outstanding = _first_value(row, _SHARES_KEYS)
            if shares_outstanding is None and market_cap is not None and price not in (None, 0):
                shares_outstanding = float(market_cap) / float(price)
            if not symbol:
                continue
            records.append(
                {
                    "ticker": symbol,
                    "name": row.get("shortName") or row.get("longName"),
                    "exchange": row.get("exchange"),
                    "market_cap": market_cap,
                    "price": price,
                    "float_shares": _first_value(row, _FLOAT_KEYS),
                    "shares_outstanding": shares_outstanding,
                    "source": "yahoo",
                    "as_of": date.today().isoformat(),
                }
            )

        frame = _normalise_snapshot(records, require_price=True)
        return frame.sort_values("market_cap", ascending=False).head(limit).reset_index(drop=True)

    def historical_universe_snapshots(
        self,
        current_snapshot: pd.DataFrame,
        lookback_days: int,
        end_date: date | None = None,
    ) -> dict[date, pd.DataFrame]:
        """Estimate daily historical market caps from Yahoo close prices.

        Yahoo's free endpoints do not provide a clean historical market-cap bulk endpoint. This
        uses the current share count from the screener snapshot and multiplies it by historical
        close prices. That keeps the provider free and modular, but it is an estimate when share
        counts changed during the lookback window.
        """
        end = end_date or date.today()
        start = end - timedelta(days=lookback_days + 10)
        current = _normalise_snapshot(current_snapshot.to_dict("records"), require_price=True)
        current = current.copy()
        current["shares_outstanding"] = pd.to_numeric(
            current.get("shares_outstanding"),
            errors="coerce",
        )
        inferred_shares = current["market_cap"] / current["price"]
        current["shares_outstanding"] = current["shares_outstanding"].fillna(inferred_shares)
        current = current.dropna(subset=["shares_outstanding"])
        current = current[current["shares_outstanding"] > 0]
        shares_by_symbol = current.set_index("ticker")["shares_outstanding"].to_dict()
        if not shares_by_symbol:
            return {}

        records_by_date: dict[date, list[dict[str, Any]]] = defaultdict(list)
        symbols = list(shares_by_symbol)
        for chunk in _chunked(symbols, YAHOO_DOWNLOAD_BATCH_SIZE):
            prices = self._download_close_prices(chunk, start, end)
            for symbol, series in prices.items():
                shares = float(shares_by_symbol[symbol])
                current_row = current[current["ticker"] == symbol].iloc[0].to_dict()
                for timestamp, close in series.dropna().items():
                    as_of = timestamp.date() if hasattr(timestamp, "date") else timestamp
                    records_by_date[as_of].append(
                        {
                            "ticker": symbol,
                            "name": current_row.get("name"),
                            "exchange": current_row.get("exchange"),
                            "market_cap": float(close) * shares,
                            "price": float(close),
                            "float_shares": current_row.get("float_shares"),
                            "shares_outstanding": shares,
                            "source": "yahoo_estimated",
                            "as_of": as_of.isoformat(),
                        }
                    )

        snapshots: dict[date, pd.DataFrame] = {}
        cutoff = end - timedelta(days=lookback_days)
        for as_of, records in records_by_date.items():
            if cutoff <= as_of <= end:
                snapshots[as_of] = _normalise_snapshot(records, require_price=True)
        return snapshots

    def _download_close_prices(
        self,
        symbols: list[str],
        start: date,
        end: date,
    ) -> dict[str, pd.Series]:
        data = self._yf.download(
            tickers=" ".join(symbols),
            start=start.isoformat(),
            end=(end + timedelta(days=1)).isoformat(),
            group_by="ticker",
            auto_adjust=False,
            progress=False,
            threads=True,
        )
        if data is None or data.empty:
            return {}

        prices: dict[str, pd.Series] = {}
        if isinstance(data.columns, pd.MultiIndex):
            for symbol in symbols:
                for price_column in ("Close", "Adj Close"):
                    key = (symbol, price_column)
                    if key in data.columns:
                        prices[symbol] = pd.to_numeric(data[key], errors="coerce")
                        break
            return prices

        if len(symbols) == 1:
            symbol = symbols[0]
            for price_column in ("Close", "Adj Close"):
                if price_column in data.columns:
                    prices[symbol] = pd.to_numeric(data[price_column], errors="coerce")
                    break
        return prices


def _normalise_snapshot(rows: list[dict[str, Any]], require_price: bool = False) -> pd.DataFrame:
    if not rows:
        raise ValueError("market data provider returned no rows")

    frame = pd.DataFrame(rows)
    rename_map = {
        "symbol": "ticker",
        "ticker": "ticker",
        "marketCap": "market_cap",
        "marketCapitalization": "market_cap",
        "intradaymarketcap": "market_cap",
        "market_cap": "market_cap",
        "regularMarketPrice": "price",
        "intradayprice": "price",
        "price": "price",
        "floatShares": "float_shares",
        "sharesOutstanding": "shares_outstanding",
    }
    valid_renames = {key: value for key, value in rename_map.items() if key in frame.columns}
    frame = frame.rename(columns=valid_renames)
    required = {"ticker", "market_cap"}
    if require_price:
        required.add("price")
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"provider snapshot missing required columns: {sorted(missing)}")
    columns = [
        column
        for column in [
            "ticker",
            "name",
            "exchange",
            "market_cap",
            "price",
            "float_shares",
            "shares_outstanding",
            "source",
            "as_of",
        ]
        if column in frame
    ]
    frame = frame[columns].copy()
    frame["ticker"] = frame["ticker"].astype(str).str.upper().str.strip()
    for column in ["market_cap", "price", "float_shares", "shares_outstanding"]:
        if column in frame:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    required_subset = ["ticker", "market_cap"] + (["price"] if require_price else [])
    frame = frame.dropna(subset=required_subset)
    frame = frame[frame["ticker"] != ""]
    frame = frame[frame["market_cap"] > 0]
    if require_price and "price" in frame:
        frame = frame[frame["price"] > 0]
    frame = frame.drop_duplicates(subset=["ticker"], keep="first")
    if frame.empty:
        raise ValueError("provider snapshot had no valid market-cap rows")
    return frame.reset_index(drop=True)


class FixtureMarketDataClient:
    """Deterministic provider used for local dry-runs and tests."""

    def current_universe_snapshot(self) -> pd.DataFrame:
        today = date.today().isoformat()
        return pd.DataFrame(
            [
                {
                    "ticker": "MSFT",
                    "market_cap": 3_100_000_000_000,
                    "price": 420,
                    "shares_outstanding": 7_380_952_381,
                    "source": "fixture",
                    "as_of": today,
                },
                {
                    "ticker": "NVDA",
                    "market_cap": 3_000_000_000_000,
                    "price": 125,
                    "shares_outstanding": 24_000_000_000,
                    "source": "fixture",
                    "as_of": today,
                },
                {
                    "ticker": "AAPL",
                    "market_cap": 2_900_000_000_000,
                    "price": 195,
                    "shares_outstanding": 14_871_794_872,
                    "source": "fixture",
                    "as_of": today,
                },
                {
                    "ticker": "AMZN",
                    "market_cap": 1_900_000_000_000,
                    "price": 180,
                    "shares_outstanding": 10_555_555_556,
                    "source": "fixture",
                    "as_of": today,
                },
            ]
        )


def build_data_client(settings: Settings) -> MarketDataClient:
    if settings.data_provider == "fixture":
        return FixtureMarketDataClient()
    if settings.data_provider == "fmp":
        return FmpMarketDataClient(settings)
    if settings.data_provider == "yahoo":
        return YahooFinanceMarketDataClient(settings)
    raise ValueError(f"unsupported DATA_PROVIDER={settings.data_provider}")


def utc_run_id(prefix: str = "rebalance") -> str:
    return f"{prefix}-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
